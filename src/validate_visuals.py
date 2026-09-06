"""Run Gemini quality gates for medical script safety and visual fit."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from generate_script import normalize_script_plan
from pipeline_paths import PipelinePaths, build_pipeline_paths


DEFAULT_REVIEW_MODEL = "gemini-2.5-flash"
DEFAULT_MIN_VISUAL_SCORE = 4
DEFAULT_MAX_SCRIPT_REVISIONS = 2
DEFAULT_GEMINI_CALL_RETRIES = 5
VIDEO_UPLOAD_TIMEOUT_SECONDS = 180
VIDEO_UPLOAD_POLL_SECONDS = 5
VALID_VIDEO_MODES = {"metadata", "video"}


def load_json_file(path: Path, missing_message: str) -> Any:
    """Load a UTF-8 JSON file with a clear missing-file error."""
    if not path.exists():
        raise FileNotFoundError(missing_message)
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_file(path: Path, data: Any) -> None:
    """Persist JSON without escaping non-English text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_script(paths: PipelinePaths) -> Dict[str, Any]:
    """Load normalized block-based script JSON."""
    return load_json_file(paths.script_file, f"Missing script file: {paths.script_file}")


def load_assets(paths: PipelinePaths) -> List[Dict[str, Any]]:
    """Load selected asset metadata."""
    return load_json_file(paths.assets_file, f"Missing assets file: {paths.assets_file}")


def load_existing_report(paths: PipelinePaths) -> Dict[str, Any]:
    """Load the current quality report if it exists."""
    if not paths.quality_review_file.exists():
        return {
            "version": 1,
            "script_review": None,
            "visual_reviews": [],
            "summary": {},
        }
    return json.loads(paths.quality_review_file.read_text(encoding="utf-8"))


def get_review_model(model: Optional[str]) -> str:
    """Resolve the Gemini model used for quality review."""
    return model or os.getenv("GEMINI_REVIEW_MODEL", DEFAULT_REVIEW_MODEL)


def get_review_mode(mode: Optional[str]) -> str:
    """Resolve visual review mode."""
    resolved = mode or os.getenv("GEMINI_QUALITY_REVIEW_MODE", "video")
    if resolved not in VALID_VIDEO_MODES:
        raise ValueError(f"Review mode must be one of {sorted(VALID_VIDEO_MODES)}.")
    return resolved


def get_min_visual_score(min_score: Optional[int]) -> int:
    """Resolve minimum acceptable Gemini visual-match score."""
    if min_score is not None:
        return min_score

    raw_value = os.getenv("GEMINI_QUALITY_MIN_SCORE", str(DEFAULT_MIN_VISUAL_SCORE))
    try:
        return max(1, min(int(raw_value), 5))
    except ValueError:
        return DEFAULT_MIN_VISUAL_SCORE


def get_max_script_revisions(max_revisions: Optional[int]) -> int:
    """Resolve how many Gemini correction rounds may run before blocking."""
    if max_revisions is not None:
        return max(0, min(max_revisions, 4))

    raw_value = os.getenv("GEMINI_MAX_SCRIPT_REVISIONS", str(DEFAULT_MAX_SCRIPT_REVISIONS))
    try:
        return max(0, min(int(raw_value), 4))
    except ValueError:
        return DEFAULT_MAX_SCRIPT_REVISIONS


def get_gemini_client() -> genai.Client:
    """Create a Gemini client from GEMINI_API_KEY."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing GEMINI_API_KEY in environment.")
    return genai.Client(api_key=api_key)


def get_gemini_call_retries() -> int:
    """Resolve transient API retry count while keeping calls bounded."""
    raw_value = os.getenv("GEMINI_CALL_RETRIES", str(DEFAULT_GEMINI_CALL_RETRIES))
    try:
        return max(3, min(int(raw_value), 8))
    except ValueError:
        return DEFAULT_GEMINI_CALL_RETRIES


def extract_text_response(response: Any) -> str:
    """Return response text across SDK response shapes."""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    try:
        return response.candidates[0].content.parts[0].text.strip()
    except Exception as error:
        prompt_feedback = getattr(response, "prompt_feedback", None)
        candidates = getattr(response, "candidates", None)
        raise ValueError(
            "Gemini response did not include text. "
            f"prompt_feedback={prompt_feedback!r}, candidates={candidates!r}"
        ) from error


def parse_json_response(response_text: str) -> Dict[str, Any]:
    """Parse strict JSON, with a defensive fallback for fenced output."""
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    first_brace = response_text.find("{")
    last_brace = response_text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return json.loads(response_text[first_brace : last_brace + 1])

    raise ValueError(f"Gemini response was not valid JSON: {response_text[:500]}")


def gemini_json(client: genai.Client, model: str, contents: Any) -> Dict[str, Any]:
    """Call Gemini with bounded retries and parse a JSON object response."""
    last_error: Optional[Exception] = None
    retries = get_gemini_call_retries()
    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            return parse_json_response(extract_text_response(response))
        except Exception as error:
            last_error = error
            if attempt >= retries:
                break
            wait_seconds = min(2 ** (attempt - 1), 8)
            print(
                f"Gemini request failed (attempt {attempt}/{retries}); "
                f"retrying in {wait_seconds}s: {error}"
            )
            time.sleep(wait_seconds)

    raise RuntimeError("Gemini request failed after retries.") from last_error


def compact_script_for_review(script: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only fields Gemini needs for script quality review."""
    return {
        "title": script.get("title", ""),
        "language": script.get("language", ""),
        "narration": script.get("narration", ""),
        "medical_sources": script.get("medical_sources", []),
        "source_reference": script.get("source_reference", {}),
        "blocks": [
            {
                "id": block.get("id", ""),
                "narration": block.get("narration", ""),
                "captions": block.get("captions", []),
                "visual_keywords": block.get("visual_keywords", []),
                "avoid_visuals": block.get("avoid_visuals", []),
            }
            for block in script.get("blocks", [])
        ],
    }


def build_medical_review_prompt(script: Dict[str, Any], reviewer_lens: str) -> str:
    """Build one independent script review prompt for the requested lens."""
    payload = compact_script_for_review(script)
    if reviewer_lens == "clinical_accuracy":
        lens_instruction = (
            "Act as a conservative pediatric clinical accuracy reviewer. Check every factual statement, "
            "the distinction between common behavior and diagnosis, the urgency of red flags, and whether "
            "the listed authoritative sources actually support the wording. Reject unsupported certainty, "
            "unsafe home treatment, missing emergency advice, or invented thresholds."
        )
    else:
        lens_instruction = (
            "Act as a native-language patient education editor with medical safety training. Check natural "
            "wording, encoding/mojibake, captions matching narration, age and symptom details, and whether a "
            "translation strengthens or weakens any reassurance or warning. Reject ambiguous or misleading wording."
        )

    return (
        "You are reviewing short-form patient education content before publication.\n"
        f"{lens_instruction}\n"
        "This pass reviews only; do not rewrite the script in this response. Be conservative and cite the exact "
        "block and wording in every finding. Return only JSON with this shape:\n"
        "{\n"
        '  "review_type": "medical_script",\n'
        '  "reviewer_lens": "",\n'
        '  "status": "approved | needs_revision | blocked",\n'
        '  "score": 5,\n'
        '  "must_not_publish": false,\n'
        '  "summary": "",\n'
        '  "checks": {\n'
        '    "claims_supported": false,\n'
        '    "reassurance_is_conditional": false,\n'
        '    "red_flags_are_proportionate": false,\n'
        '    "captions_match_narration": false,\n'
        '    "language_is_natural_and_intact": false,\n'
        '    "visual_keywords_are_safe": false\n'
        "  },\n"
        '  "findings": [\n'
        '    {"severity": "critical | major | minor", "block_id": "", "issue": "", "evidence": "", "recommendation": ""}\n'
        "  ]\n"
        "}\n"
        "Use score 5 for publication-ready with no meaningful issue, 4 for safe with only optional polish, "
        "3 for mandatory revision, and 1-2 for blocked or unsafe. Status must be needs_revision for any "
        "mandatory correction. Never return approved with score 1-3.\n\n"
        f"SCRIPT_JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def combine_script_reviews(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine independent Gemini reviews using the most conservative result."""
    statuses = [review.get("status") for review in reviews]
    if any(review.get("must_not_publish") is True or status == "blocked" for review, status in zip(reviews, statuses)):
        status = "blocked"
    elif any(status == "needs_revision" for status in statuses):
        status = "needs_revision"
    else:
        status = "approved"

    findings = []
    for review in reviews:
        reviewer_lens = review.get("reviewer_lens", "unknown")
        for finding in review.get("findings") or []:
            finding_with_lens = dict(finding)
            finding_with_lens["reviewer_lens"] = reviewer_lens
            findings.append(finding_with_lens)

    return {
        "review_type": "medical_script_panel",
        "status": status,
        "score": min((coerce_score(review.get("score")) for review in reviews), default=0),
        "must_not_publish": any(review.get("must_not_publish") is True for review in reviews),
        "summary": " | ".join(str(review.get("summary", "")).strip() for review in reviews),
        "reviews": reviews,
        "findings": findings,
    }


def run_script_review_panel(
    client: genai.Client,
    model: str,
    script: Dict[str, Any],
) -> Dict[str, Any]:
    """Run independent clinical and language-integrity review passes."""
    reviews = []
    for reviewer_lens in ("clinical_accuracy", "language_integrity"):
        print(f"Gemini script reviewer: {reviewer_lens}")
        review = gemini_json(client, model, build_medical_review_prompt(script, reviewer_lens))
        review["reviewer_lens"] = review.get("reviewer_lens") or reviewer_lens
        reviews.append(review)
    return combine_script_reviews(reviews)


def build_script_revision_prompt(script: Dict[str, Any], panel_review: Dict[str, Any]) -> str:
    """Ask Gemini to correct only issues found by the independent review panel."""
    return (
        "You are the senior pediatric patient-education editor. Correct every mandatory finding in the review "
        "while preserving the topic, target language, block IDs, source URLs, calm tone, and short-form structure. "
        "Do not add diagnosis, medication doses, treatment claims, or unsupported numerical thresholds. "
        "Each caption must be a short phrase copied or faithfully condensed from its block narration. "
        "Keep visual keywords concrete, literal, non-graphic, and written in English for stock-footage search. "
        "Return a complete corrected script and a concise change log. Return only JSON with this shape:\n"
        "{\n"
        '  "revised_script": {"title": "", "language": "", "medical_sources": [], "source_reference": {}, "blocks": []},\n'
        '  "changes": [{"block_id": "", "reason": "", "before": "", "after": ""}]\n'
        "}\n\n"
        f"CURRENT_SCRIPT:\n{json.dumps(compact_script_for_review(script), ensure_ascii=False, indent=2)}\n\n"
        f"PANEL_REVIEW:\n{json.dumps(panel_review, ensure_ascii=False, indent=2)}"
    )


def revise_script(
    client: genai.Client,
    model: str,
    script: Dict[str, Any],
    panel_review: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate and validate one corrected script revision."""
    revision_response = gemini_json(client, model, build_script_revision_prompt(script, panel_review))
    revised_plan = revision_response.get("revised_script")
    if not isinstance(revised_plan, dict):
        raise ValueError("Gemini revision did not return a revised_script object.")

    revised_plan["medical_sources"] = script.get("medical_sources", [])
    revised_plan["source_reference"] = script.get("source_reference", {})
    revised_plan.pop("narration", None)
    revised_script = normalize_script_plan(revised_plan)

    original_ids = [block.get("id") for block in script.get("blocks", [])]
    revised_ids = [block.get("id") for block in revised_script.get("blocks", [])]
    if revised_script.get("language") != script.get("language"):
        raise ValueError("Gemini revision changed the script language.")
    if revised_ids != original_ids:
        raise ValueError("Gemini revision changed the authored block IDs or order.")

    original_block_map = {block["id"]: block for block in script.get("blocks", [])}
    for block in revised_script.get("blocks", []):
        combined_avoid_visuals = []
        seen = set()
        for item in [
            *original_block_map[block["id"]].get("avoid_visuals", []),
            *block.get("avoid_visuals", []),
        ]:
            lowered = item.lower()
            if lowered not in seen:
                seen.add(lowered)
                combined_avoid_visuals.append(item)
        block["avoid_visuals"] = combined_avoid_visuals

    return revised_script, revision_response


def review_script_quality(
    paths: PipelinePaths,
    model: Optional[str] = None,
    fail_on_blocked: bool = True,
    auto_revise: bool = True,
    max_revisions: Optional[int] = None,
) -> Dict[str, Any]:
    """Review, automatically correct, and re-review a normalized script."""
    script = load_script(paths)
    resolved_model = get_review_model(model)
    client = get_gemini_client()
    resolved_max_revisions = get_max_script_revisions(max_revisions) if auto_revise else 0

    shutil.copy2(paths.script_file, paths.original_script_file)

    history = []
    review = {}
    for revision_round in range(resolved_max_revisions + 1):
        review = run_script_review_panel(client, resolved_model, script)
        history_entry = {
            "round": revision_round,
            "script": script,
            "review": review,
            "revision": None,
        }
        history.append(history_entry)

        if not is_script_blocked(review):
            break
        if revision_round >= resolved_max_revisions:
            break

        print(f"Gemini script correction round {revision_round + 1}/{resolved_max_revisions}")
        script, revision_response = revise_script(client, resolved_model, script, review)
        history_entry["revision"] = revision_response
        save_json_file(paths.script_file, script)
        paths.narration_text_file.write_text(script["narration"], encoding="utf-8")

    save_json_file(
        paths.script_revision_history_file,
        {
            "model": resolved_model,
            "max_revisions": resolved_max_revisions,
            "rounds": history,
        },
    )
    report = load_existing_report(paths)

    report.update(
        {
            "version": 2,
            "model": resolved_model,
            "script_file": str(paths.script_file),
            "original_script_file": str(paths.original_script_file),
            "script_revision_history_file": str(paths.script_revision_history_file),
            "assets_file": str(paths.assets_file),
        }
    )
    report["script_review"] = review
    report["script_revision_count"] = sum(1 for entry in history if entry.get("revision"))
    report["summary"] = build_quality_summary(report, get_min_visual_score(None))
    save_json_file(paths.quality_review_file, report)
    print(f"Created {paths.quality_review_file}")

    if fail_on_blocked and is_script_blocked(review):
        raise RuntimeError("Gemini medical script review blocked this run. See quality_review.json.")

    return review


def is_script_blocked(review: Dict[str, Any]) -> bool:
    """Return whether the script review should stop the pipeline."""
    if review.get("must_not_publish") is True:
        return True
    if review.get("status") in {"needs_revision", "blocked"}:
        return True
    if coerce_score(review.get("score")) < 4:
        return True
    return any(
        finding.get("severity") in {"critical", "major"}
        for finding in review.get("findings", [])
    )


def build_asset_lookup(assets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index selected asset metadata by block_id."""
    return {asset.get("block_id", ""): asset for asset in assets}


def get_asset_path(paths: PipelinePaths, block_id: str) -> Path:
    """Return expected downloaded video path for a block."""
    return paths.assets_dir / f"{block_id}.mp4"


def compact_asset_for_visual_review(asset: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Exclude rejected candidate metadata and unrelated descriptions from review input."""
    if not asset:
        return {}
    fields = (
        "block_id",
        "keyword",
        "pexels_video_id",
        "pexels_page_url",
        "duration",
        "width",
        "height",
        "delivery_width",
        "delivery_height",
    )
    return {field: asset.get(field) for field in fields if asset.get(field) is not None}


def wait_for_uploaded_file(client: genai.Client, uploaded_file: Any) -> Any:
    """Wait until a Gemini-uploaded file is ready for model use."""
    started_at = time.time()
    current_file = uploaded_file

    while time.time() - started_at < VIDEO_UPLOAD_TIMEOUT_SECONDS:
        state = getattr(current_file, "state", None)
        state_name = getattr(state, "name", str(state or "")).upper()
        if not state_name or state_name in {"ACTIVE", "READY", "SUCCEEDED"}:
            return current_file
        if state_name in {"FAILED", "ERROR"}:
            raise RuntimeError(f"Gemini file upload failed: {state_name}")

        time.sleep(VIDEO_UPLOAD_POLL_SECONDS)
        current_file = client.files.get(name=current_file.name)

    raise TimeoutError(f"Timed out waiting for Gemini file processing: {uploaded_file.name}")


def upload_video_for_review(client: genai.Client, video_path: Path) -> Any:
    """Upload one local MP4 file for Gemini visual review."""
    uploaded_file = client.files.upload(
        file=str(video_path),
        config=types.UploadFileConfig(mime_type="video/mp4", display_name=video_path.name),
    )
    return wait_for_uploaded_file(client, uploaded_file)


def build_visual_review_prompt(
    script: Dict[str, Any],
    block: Dict[str, Any],
    asset: Optional[Dict[str, Any]],
    mode: str,
    video_path: Optional[Path],
) -> str:
    """Build one block-level visual-fit review prompt."""
    payload = {
        "title": script.get("title", ""),
        "language": script.get("language", ""),
        "review_mode": mode,
        "block": {
            "id": block.get("id", ""),
            "narration": block.get("narration", ""),
            "captions": block.get("captions", []),
            "visual_keywords": block.get("visual_keywords", []),
            "avoid_visuals": block.get("avoid_visuals", []),
        },
        "selected_asset": compact_asset_for_visual_review(asset),
        "local_video_path": str(video_path) if video_path else None,
    }
    mode_instruction = (
        "You are given the actual selected video file. Judge what the visible footage shows."
        if mode == "video"
        else "You are given only asset metadata, not the actual video. Be explicit that this is metadata-only."
    )
    return (
        "You are a strict quality reviewer for a legitimate, non-sexual pediatric health-education video. "
        "The material concerns routine infant development, feeding observation, and caregiver safety.\n"
        f"{mode_instruction}\n"
        "Inspect the full visible clip, not only its first frame. Compare the subject, approximate age group, "
        "action, setting, emotional tone, and implied medical meaning against the block narration and captions. "
        "A generic family or clinic clip is not a match when the block needs a specific observable action. "
        "Flag off-topic, repetitive, too alarming, graphic, misleading, diagnosis-implying, or medically "
        "unsupported visuals. Reject footage centered on adults, older children, procedures, distress, or "
        "unrelated lifestyle activity when the script is about a calm infant behavior. "
        "Do not rewrite the script. Return only JSON with this shape:\n"
        "{\n"
        '  "review_type": "visual_match",\n'
        '  "block_id": "",\n'
        '  "status": "approved | needs_replacement | blocked",\n'
        '  "match_score": 5,\n'
        '  "medical_safety_score": 5,\n'
        '  "what_video_shows": "",\n'
        '  "summary": "",\n'
        '  "mismatch_reasons": [],\n'
        '  "suggested_keywords": [],\n'
        '  "avoid_visuals": []\n'
        "}\n"
        "Use match_score 5 only when the footage clearly and literally supports the block, 4 when it is a safe "
        "close contextual match, 3 for generic or partly mismatched footage, and 1-2 for unrelated footage. "
        "If status is approved, both scores must be 4 or 5. Never return approved with a score of 1-3.\n\n"
        f"REVIEW_JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def fallback_missing_asset_review(block: Dict[str, Any]) -> Dict[str, Any]:
    """Build a deterministic blocked review when an asset is missing."""
    return {
        "review_type": "visual_match",
        "block_id": block.get("id", ""),
        "status": "blocked",
        "match_score": 1,
        "medical_safety_score": 1,
        "what_video_shows": "",
        "summary": "No selected or downloaded asset exists for this block.",
        "mismatch_reasons": ["missing_asset"],
        "suggested_keywords": block.get("visual_keywords", []),
        "avoid_visuals": block.get("avoid_visuals", []),
    }


def review_one_visual(
    client: genai.Client,
    model: str,
    script: Dict[str, Any],
    block: Dict[str, Any],
    asset: Optional[Dict[str, Any]],
    paths: PipelinePaths,
    mode: str,
) -> Dict[str, Any]:
    """Run one block-level Gemini visual review."""
    block_id = block.get("id", "")
    video_path = get_asset_path(paths, block_id)

    if not asset:
        return fallback_missing_asset_review(block)
    if mode == "video" and not video_path.exists():
        return fallback_missing_asset_review(block)

    if mode == "video":
        uploaded_file = upload_video_for_review(client, video_path)
        contents = [
            uploaded_file,
            build_visual_review_prompt(script, block, asset, mode, video_path),
        ]
    else:
        contents = build_visual_review_prompt(script, block, asset, mode, None)

    review = gemini_json(client, model, contents)
    review["block_id"] = review.get("block_id") or block_id
    review["selected_keyword"] = asset.get("keyword")
    review["pexels_page_url"] = asset.get("pexels_page_url")
    review["local_video_path"] = str(video_path) if video_path.exists() else None
    return review


def visual_review_failed(review: Dict[str, Any], min_score: int) -> bool:
    """Return whether a visual review should stop rendering."""
    status = review.get("status")
    match_score = coerce_score(review.get("match_score"))
    medical_score = coerce_score(review.get("medical_safety_score"))
    return status in {"needs_replacement", "blocked"} or match_score < min_score or medical_score < min_score


def coerce_score(value: Any) -> int:
    """Parse Gemini scores defensively."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group(0))
    return 0


def build_quality_summary(report: Dict[str, Any], min_visual_score: int) -> Dict[str, Any]:
    """Build a compact pass/fail summary for the quality report."""
    script_review = report.get("script_review") or {}
    visual_reviews = report.get("visual_reviews") or []
    failed_visuals = [
        review.get("block_id", "")
        for review in visual_reviews
        if visual_review_failed(review, min_visual_score)
    ]
    script_reviewed = bool(script_review)
    visual_review_complete = bool(visual_reviews)
    script_blocked = script_reviewed and is_script_blocked(script_review)
    script_passed = script_reviewed and not script_blocked
    visual_passed = visual_review_complete and not failed_visuals
    passed = script_passed and visual_passed

    return {
        "passed": passed,
        "script_reviewed": script_reviewed,
        "script_passed": script_passed,
        "script_blocked": script_blocked,
        "visual_review_complete": visual_review_complete,
        "visual_passed": visual_passed,
        "visual_review_count": len(visual_reviews),
        "failed_visual_block_ids": failed_visuals,
        "min_visual_score": min_visual_score,
    }


def review_visual_quality(
    paths: PipelinePaths,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    min_score: Optional[int] = None,
    fail_on_blocked: bool = True,
    block_ids: Optional[List[str]] = None,
    reuse_passed: bool = True,
) -> List[Dict[str, Any]]:
    """Review selected/downloaded visuals against the normalized script."""
    script = load_script(paths)
    assets = load_assets(paths)
    asset_lookup = build_asset_lookup(assets)
    resolved_model = get_review_model(model)
    resolved_mode = get_review_mode(mode)
    resolved_min_score = get_min_visual_score(min_score)
    client = get_gemini_client()
    report = load_existing_report(paths)
    existing_reviews = {
        review.get("block_id", ""): review
        for review in report.get("visual_reviews", [])
    }
    visual_review_map = dict(existing_reviews)
    target_block_ids = set(block_ids or [block.get("id", "") for block in script.get("blocks", [])])

    for block in script.get("blocks", []):
        block_id = block.get("id", "")
        if block_id not in target_block_ids:
            continue

        existing_review = existing_reviews.get(block_id)
        if (
            reuse_passed
            and existing_review
            and not visual_review_failed(existing_review, resolved_min_score)
        ):
            print(f"Gemini visual review: {block_id} already passed, reusing existing review")
            continue

        print(f"Gemini visual review: {block_id}")
        visual_review_map[block_id] = review_one_visual(
            client=client,
            model=resolved_model,
            script=script,
            block=block,
            asset=asset_lookup.get(block_id),
            paths=paths,
            mode=resolved_mode,
        )
        save_visual_report(
            paths=paths,
            report=report,
            script=script,
            visual_review_map=visual_review_map,
            resolved_model=resolved_model,
            resolved_mode=resolved_mode,
            resolved_min_score=resolved_min_score,
        )

    visual_reviews = save_visual_report(
        paths=paths,
        report=report,
        script=script,
        visual_review_map=visual_review_map,
        resolved_model=resolved_model,
        resolved_mode=resolved_mode,
        resolved_min_score=resolved_min_score,
    )
    print(f"Updated {paths.quality_review_file}")

    failed_reviews = [
        review for review in visual_reviews if visual_review_failed(review, resolved_min_score)
    ]
    if fail_on_blocked and failed_reviews:
        failed_ids = ", ".join(review.get("block_id", "") for review in failed_reviews)
        raise RuntimeError(f"Gemini visual review failed for blocks: {failed_ids}. See quality_review.json.")

    return visual_reviews


def save_visual_report(
    paths: PipelinePaths,
    report: Dict[str, Any],
    script: Dict[str, Any],
    visual_review_map: Dict[str, Dict[str, Any]],
    resolved_model: str,
    resolved_mode: str,
    resolved_min_score: int,
) -> List[Dict[str, Any]]:
    """Persist current visual reviews in script block order."""
    visual_reviews = [
        visual_review_map[block.get("id", "")]
        for block in script.get("blocks", [])
        if block.get("id", "") in visual_review_map
    ]
    report.update(
        {
            "version": 1,
            "model": resolved_model,
            "video_review_mode": resolved_mode,
            "script_file": str(paths.script_file),
            "assets_file": str(paths.assets_file),
        }
    )
    report["visual_reviews"] = visual_reviews
    report["summary"] = build_quality_summary(report, resolved_min_score)
    save_json_file(paths.quality_review_file, report)
    return visual_reviews


def run_full_quality_review(
    paths: PipelinePaths,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    min_score: Optional[int] = None,
    fail_on_blocked: bool = True,
) -> Dict[str, Any]:
    """Run script and visual reviews for an already generated run."""
    review_script_quality(paths, model=model, fail_on_blocked=fail_on_blocked)
    review_visual_quality(
        paths,
        model=model,
        mode=mode,
        min_score=min_score,
        fail_on_blocked=fail_on_blocked,
        reuse_passed=True,
    )
    return load_existing_report(paths)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run Gemini quality review for one pipeline output.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    parser.add_argument(
        "--stage",
        choices=["script", "visual", "all"],
        default="all",
        help="Review stage to run.",
    )
    parser.add_argument("--model", default=None, help="Gemini review model override.")
    parser.add_argument(
        "--video-mode",
        choices=sorted(VALID_VIDEO_MODES),
        default=None,
        help="Use metadata-only review or upload downloaded videos for visual review.",
    )
    parser.add_argument("--min-score", type=int, default=None, help="Minimum acceptable visual score.")
    parser.add_argument(
        "--blocks",
        default=None,
        help="Comma-separated block IDs to review. Other existing reviews are preserved.",
    )
    parser.add_argument(
        "--force-visual-review",
        action="store_true",
        help="Review selected blocks even when an existing review already passed.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Write the report without raising on blocked reviews.",
    )
    return parser.parse_args()


def main() -> None:
    """Run quality review from the command line."""
    args = parse_args()
    paths = build_pipeline_paths(args.input, args.output)
    fail_on_blocked = not args.allow_failures

    if args.stage == "script":
        review_script_quality(paths, model=args.model, fail_on_blocked=fail_on_blocked)
    elif args.stage == "visual":
        review_visual_quality(
            paths,
            model=args.model,
            mode=args.video_mode,
            min_score=args.min_score,
            fail_on_blocked=fail_on_blocked,
            block_ids=parse_block_ids(args.blocks),
            reuse_passed=not args.force_visual_review,
        )
    else:
        run_full_quality_review(
            paths,
            model=args.model,
            mode=args.video_mode,
            min_score=args.min_score,
            fail_on_blocked=fail_on_blocked,
        )


def parse_block_ids(raw_value: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated block ID list."""
    if not raw_value:
        return None
    return [item.strip() for item in raw_value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
