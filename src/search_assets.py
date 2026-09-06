"""Search Pexels assets for the authored block plan."""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Set

import requests
from dotenv import load_dotenv

from pipeline_paths import PipelinePaths, build_pipeline_paths

load_dotenv()


DEFAULT_RESULTS_PER_KEYWORD = 5
MIN_REASONABLE_DURATION = 4
MAX_REASONABLE_DURATION = 30
GEMINI_PREVIEW_CANDIDATE_LIMIT = 8
DELIVERY_WIDTH = 1080
DELIVERY_HEIGHT = 1920


def load_script(paths: PipelinePaths) -> dict:
    """Load the normalized script file."""
    if not paths.script_file.exists():
        raise FileNotFoundError(f"Missing script file: {paths.script_file}")

    return json.loads(paths.script_file.read_text(encoding="utf-8"))


def get_search_keywords(block: Dict, keyword_overrides: Optional[List[str]] = None) -> List[str]:
    """Return keyword candidates in preferred search order."""
    seen = set()
    ordered_keywords = []

    for keyword in [*(keyword_overrides or []), *block.get("visual_keywords", [])]:
        lowered = keyword.lower()
        if keyword and lowered not in seen:
            seen.add(lowered)
            ordered_keywords.append(keyword)

    return ordered_keywords


def select_downloadable_mp4(video_files: List[Dict]) -> Optional[Dict]:
    """Choose a portrait MP4 closest to the delivery size without defaulting to 4K."""
    mp4_files = [
        video_file
        for video_file in video_files
        if video_file.get("file_type") == "video/mp4" and video_file.get("link")
    ]

    if not mp4_files:
        return None

    target_aspect_ratio = DELIVERY_WIDTH / DELIVERY_HEIGHT

    def rank(video_file: Dict) -> tuple:
        width = int(video_file.get("width") or 0)
        height = int(video_file.get("height") or 0)
        portrait = height >= width > 0
        meets_delivery_size = width >= DELIVERY_WIDTH and height >= DELIVERY_HEIGHT
        aspect_ratio = width / height if height else 0
        aspect_distance = abs(aspect_ratio - target_aspect_ratio)

        if portrait and meets_delivery_size:
            quality_class = 4
            size_score = -(
                abs(width - DELIVERY_WIDTH) / DELIVERY_WIDTH
                + abs(height - DELIVERY_HEIGHT) / DELIVERY_HEIGHT
            )
        elif portrait:
            quality_class = 3
            size_score = width * height
        elif meets_delivery_size:
            quality_class = 2
            size_score = -(width * height)
        else:
            quality_class = 1
            size_score = width * height

        return quality_class, size_score, -aspect_distance

    return max(mp4_files, key=rank)


def get_results_per_keyword() -> int:
    """Return the configured number of Pexels candidates to inspect per keyword."""
    raw_value = os.getenv("PEXELS_RESULTS_PER_KEYWORD", str(DEFAULT_RESULTS_PER_KEYWORD))
    try:
        return max(1, min(int(raw_value), 15))
    except ValueError:
        return DEFAULT_RESULTS_PER_KEYWORD


def normalize_for_match(text: str) -> str:
    """Normalize free text for coarse metadata matching."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def contains_avoid_term(candidate_text: str, avoid_visuals: List[str]) -> bool:
    """Return whether candidate metadata contains an avoid hint."""
    normalized_candidate = normalize_for_match(candidate_text)
    return any(normalize_for_match(term) in normalized_candidate for term in avoid_visuals if term)


def score_candidate(video: Dict, selected_file: Dict, keyword_index: int, avoid_visuals: List[str]) -> int:
    """Score a Pexels candidate using metadata available before Gemini review."""
    width = selected_file.get("width") or video.get("width") or 0
    height = selected_file.get("height") or video.get("height") or 0
    duration = video.get("duration") or 0
    score = 0

    if height and width:
        aspect_ratio = height / max(width, 1)
        if aspect_ratio >= 1.4:
            score += 400
        elif aspect_ratio >= 1.0:
            score += 200
        else:
            score -= 150

    score += min(int(height or 0), 2160) // 4
    score -= keyword_index * 40

    if MIN_REASONABLE_DURATION <= duration <= MAX_REASONABLE_DURATION:
        score += 120
    elif duration > MAX_REASONABLE_DURATION:
        score -= 60

    candidate_text = " ".join(
        str(video.get(field, "")) for field in ["url", "image", "id", "duration"]
    )
    if contains_avoid_term(candidate_text, avoid_visuals):
        score -= 1000

    return score


def summarize_candidate(video: Dict, selected_file: Dict, keyword: str, score: int) -> Dict:
    """Keep compact metadata for later Gemini quality review."""
    return {
        "video_id": video.get("id"),
        "keyword": keyword,
        "score": score,
        "pexels_page_url": video.get("url"),
        "preview_image": video.get("image"),
        "duration": video.get("duration"),
        "width": selected_file.get("width"),
        "height": selected_file.get("height"),
    }


def rank_candidates_with_gemini(
    block: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Use candidate preview images to reject obvious semantic mismatches before download."""
    from google.genai import types

    from validate_visuals import coerce_score, gemini_json, get_gemini_client, get_review_model

    preview_candidates = candidates[:GEMINI_PREVIEW_CANDIDATE_LIMIT]
    candidate_metadata = []
    contents: List[Any] = []

    for index, candidate in enumerate(preview_candidates, start=1):
        video = candidate["video"]
        preview_url = video.get("image")
        if not preview_url:
            continue
        try:
            response = requests.get(preview_url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            print(f"Could not load Pexels preview {video.get('id')}: {error}")
            continue

        mime_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        metadata = {
            "candidate_number": index,
            "video_id": video.get("id"),
            "keyword": candidate.get("keyword"),
            "pexels_page_url": video.get("url"),
            "duration": video.get("duration"),
        }
        candidate_metadata.append(metadata)
        contents.append(f"CANDIDATE {index}: {json.dumps(metadata, ensure_ascii=False)}")
        contents.append(types.Part.from_bytes(data=response.content, mime_type=mime_type))

    if not candidate_metadata:
        return None, None

    prompt = (
        "You are pre-screening Pexels stock-video candidates for one pediatric education block. Each attached "
        "image is the preview for the labeled candidate immediately before it. Select the candidate whose visible "
        "subject, approximate age, action, setting, and emotional tone most literally match the narration. Reject "
        "adult subjects, older children, distress, procedures, or unrelated actions. A preview cannot prove the "
        "whole clip matches, so this is only a preliminary screen. Return selected_video_id null if none is a close match. "
        "Return only JSON:\n"
        "{\n"
        '  "selected_video_id": 123,\n'
        '  "confidence": 5,\n'
        '  "visible_match_reason": "",\n'
        '  "rejected_video_ids": [],\n'
        '  "limitations": "preview image only"\n'
        "}\n"
        "Use confidence 5 for a clear literal preview match, 4 for a close match, 3 for generic but plausible, "
        "and 1-2 when all candidates are weak. Only select a supplied candidate with confidence 4 or 5.\n\n"
        f"BLOCK:\n{json.dumps({'id': block.get('id'), 'narration': block.get('narration'), 'captions': block.get('captions'), 'visual_keywords': block.get('visual_keywords'), 'avoid_visuals': block.get('avoid_visuals')}, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE_METADATA:\n{json.dumps(candidate_metadata, ensure_ascii=False, indent=2)}"
    )
    client = get_gemini_client()
    try:
        review = gemini_json(client, get_review_model(model), [prompt, *contents])
    finally:
        client.close()
    selected_video_id = str(review.get("selected_video_id"))
    supplied_ids = {str(item["video_id"]) for item in candidate_metadata}
    rejected_ids = {str(item) for item in review.get("rejected_video_ids", [])}
    if coerce_score(review.get("confidence")) < 4 or selected_video_id not in supplied_ids or selected_video_id in rejected_ids:
        return None, review
    selected_candidate = next(
        (
            candidate
            for candidate in preview_candidates
            if str(candidate["video"].get("id")) == selected_video_id
        ),
        None,
    )
    return selected_candidate, review


def search_videos(keyword: str, headers: dict, per_page: int, portrait_only: bool = True) -> dict:
    """Search Pexels for a single keyword."""
    params = {"query": keyword, "per_page": per_page}
    if portrait_only:
        params["orientation"] = "portrait"

    response = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def search_assets(
    paths: PipelinePaths,
    block_ids: Optional[List[str]] = None,
    keyword_overrides: Optional[Dict[str, List[str]]] = None,
    excluded_video_ids: Optional[Set[str]] = None,
    preserve_existing: bool = True,
    use_gemini_ranking: bool = False,
    review_model: Optional[str] = None,
) -> List[Dict]:
    """Search all or selected blocks, preserving approved assets during retries."""
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing PEXELS_API_KEY in environment.")

    paths.ensure_directories()
    data = load_script(paths)
    headers = {"Authorization": api_key}
    target_block_ids = set(block_ids or [block.get("id", "") for block in data.get("blocks", [])])
    keyword_overrides = keyword_overrides or {}
    excluded_video_ids = {str(video_id) for video_id in (excluded_video_ids or set())}
    existing_results = []
    if preserve_existing and paths.assets_file.exists():
        existing_results = json.loads(paths.assets_file.read_text(encoding="utf-8"))
    existing_result_map = {
        result.get("block_id", ""): result
        for result in existing_results
    }
    result_map = {
        result.get("block_id", ""): result
        for result in existing_results
        if result.get("block_id", "") not in target_block_ids
    }
    def save_progress():
        from pipeline_state import atomic_json
        atomic_json(paths.assets_file, [result_map[b["id"]] for b in data["blocks"] if b["id"] in result_map])

    per_page = get_results_per_keyword()

    for block_index, block in enumerate(data.get("blocks", []), start=1):
        block_id = block.get("id", "")
        if block_id not in target_block_ids:
            continue

        search_keywords = get_search_keywords(block, keyword_overrides.get(block_id))
        avoid_visuals = block.get("avoid_visuals", [])
        candidates = []

        for keyword_index, keyword in enumerate(search_keywords):
            result = search_videos(keyword, headers=headers, per_page=per_page)
            videos = result.get("videos") or []

            if not videos:
                result = search_videos(
                    keyword,
                    headers=headers,
                    per_page=per_page,
                    portrait_only=False,
                )
                videos = result.get("videos") or []

            if not videos:
                continue

            for video in videos:
                if str(video.get("id")) in excluded_video_ids:
                    continue
                selected_file = select_downloadable_mp4(video.get("video_files", []))

                if not selected_file:
                    continue

                score = score_candidate(
                    video=video,
                    selected_file=selected_file,
                    keyword_index=keyword_index,
                    avoid_visuals=avoid_visuals,
                )
                candidates.append(
                    {
                        "video": video,
                        "selected_file": selected_file,
                        "keyword": keyword,
                        "score": score,
                    }
                )

        if not candidates:
            print(f"No downloadable asset found for block {block['id']}: {search_keywords}")
            result_map.pop(block_id, None)
            continue

        unique = {}
        for candidate in sorted(candidates, key=lambda candidate: candidate["score"], reverse=True):
            unique.setdefault(str(candidate["video"]["id"]), candidate)
        candidates = list(unique.values())
        preview_review = None
        selected_candidate = None
        if use_gemini_ranking:
            print(f"Gemini preview ranking: {block_id}")
            selected_candidate, preview_review = rank_candidates_with_gemini(
                block,
                candidates,
                model=review_model,
            )
            if selected_candidate is None:
                from pipeline_state import atomic_json
                atomic_json(paths.output_dir / f"preview_rejection_{block_id}.json", preview_review or {"reason": "no_loadable_previews"})
                save_progress()
                raise RuntimeError(f"No acceptable visual preview for {block_id}. Refine keywords; unsafe fallback is disabled.")
        selected_candidate = selected_candidate or candidates[0]
        selected_video = selected_candidate["video"]
        selected_file = selected_candidate["selected_file"]
        selected_keyword = selected_candidate["keyword"]
        selected_result = {
            "block_id": block["id"],
            "block_index": block_index,
            "captions": block.get("captions", []),
            "narration": block.get("narration", ""),
            "keyword": selected_keyword,
            "search_keywords": search_keywords,
            "avoid_visuals": avoid_visuals,
            "pexels_video_id": selected_video.get("id"),
            "pexels_page_url": selected_video.get("url"),
            "preview_image": selected_video.get("image"),
            "duration": selected_video.get("duration"),
            "download_url": selected_file.get("link"),
            "width": selected_file.get("width"),
            "height": selected_file.get("height"),
            "selection_score": selected_candidate["score"],
            "candidates_considered": len(candidates),
            "candidate_summary": [
                summarize_candidate(
                    video=candidate["video"],
                    selected_file=candidate["selected_file"],
                    keyword=candidate["keyword"],
                    score=candidate["score"],
                )
                for candidate in candidates[:5]
            ],
            "gemini_review_status": "pending",
            "gemini_preview_review": preview_review,
        }

        previous_result = existing_result_map.get(block_id) or {}
        previous_video_id = previous_result.get("pexels_video_id")
        if (
            previous_video_id is not None
            and str(previous_video_id) != str(selected_video.get("id"))
        ):
            previous_file = paths.assets_dir / f"{block_id}.mp4"
            if previous_file.exists():
                archived_file = paths.assets_dir / f"{block_id}.superseded-{previous_video_id}.mp4"
                suffix = 2
                while archived_file.exists():
                    archived_file = paths.assets_dir / (
                        f"{block_id}.superseded-{previous_video_id}-{suffix}.mp4"
                    )
                    suffix += 1
                previous_file.rename(archived_file)
                print(f"Archived superseded asset: {archived_file}")

        print(
            f"Block {block['id']}: selected keyword '{selected_keyword}' "
            f"from {len(candidates)} candidates"
        )
        result_map[block_id] = selected_result
        save_progress()

    results = [
        result_map[block.get("id", "")]
        for block in data.get("blocks", [])
        if block.get("id", "") in result_map
    ]
    paths.assets_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {paths.assets_file}")
    return results


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Search stock assets for one pipeline run.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Search and save stock assets."""
    args = parse_args()
    search_assets(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
