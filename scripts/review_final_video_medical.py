"""Review rendered final videos with Gemini and write a text report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from moviepy import VideoFileClip  # noqa: E402
from google.genai import types  # noqa: E402

from validate_visuals import (  # noqa: E402
    gemini_json,
    get_gemini_client,
    get_review_model,
    upload_video_for_review,
    wait_for_uploaded_file,
)


DEFAULT_OUTPUTS = [
    REPO_ROOT / "output" / "bilingual_toddler_speech" / "ko",
    REPO_ROOT / "output" / "bilingual_toddler_speech" / "en",
    REPO_ROOT / "output" / "bilingual_toddler_speech" / "es",
]


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def compact_script(script: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": script.get("title", ""),
        "language": script.get("language", ""),
        "narration": script.get("narration", ""),
        "medical_sources": script.get("medical_sources", []),
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


def get_review_topic(script: Dict[str, Any], video_path: Path) -> str:
    """Build a topic label from the actual rendered script metadata."""
    title = str(script.get("title") or "").strip()
    language = str(script.get("language") or "").strip()
    fallback_topic = video_path.parent.parent.name.replace("_", " ")

    if title and language:
        return f"{title} ({language})"
    return title or fallback_topic


def extract_contact_sheet(video_path: Path, output_dir: Path, sample_count: int = 8) -> Path:
    """Create a time-ordered contact sheet from the rendered final video."""
    contact_sheet_path = output_dir / "medical_review_contact_sheet.jpg"
    frames: List[Image.Image] = []

    with VideoFileClip(str(video_path)) as clip:
        duration = max(float(clip.duration or 0), 0.1)
        for index in range(sample_count):
            timestamp = duration * (index + 1) / (sample_count + 1)
            image = Image.fromarray(clip.get_frame(timestamp)).convert("RGB")
            image.thumbnail((240, 426), Image.Resampling.LANCZOS)

            frame = Image.new("RGB", (240, 456), "white")
            frame.paste(image, ((240 - image.width) // 2, 0))
            draw = ImageDraw.Draw(frame)
            draw.rectangle((0, 426, 240, 456), fill=(0, 0, 0))
            draw.text((8, 434), f"{timestamp:05.1f}s", fill=(255, 255, 255))
            frames.append(frame)

    columns = 4
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 240, rows * 456), "white")
    for index, frame in enumerate(frames):
        x = (index % columns) * 240
        y = (index // columns) * 456
        sheet.paste(frame, (x, y))

    sheet.save(contact_sheet_path, quality=92)
    return contact_sheet_path


def parse_srt_midpoints(captions_path: Path) -> List[Dict[str, Any]]:
    """Return every caption with a midpoint timestamp for visual QA."""
    timestamp_pattern = re.compile(
        r"(?P<start>\d{2}:\d{2}:\d{2},\d{3}) --> (?P<end>\d{2}:\d{2}:\d{2},\d{3})"
    )

    def to_seconds(value: str) -> float:
        hours, minutes, seconds_and_ms = value.split(":")
        seconds, milliseconds = seconds_and_ms.split(",")
        return (
            int(hours) * 3600
            + int(minutes) * 60
            + int(seconds)
            + int(milliseconds) / 1000
        )

    entries = []
    raw_text = captions_path.read_text(encoding="utf-8-sig").strip()
    for block in raw_text.split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        match = timestamp_pattern.fullmatch(lines[1])
        if not match:
            continue
        start = to_seconds(match.group("start"))
        end = to_seconds(match.group("end"))
        entries.append(
            {
                "index": int(lines[0]),
                "timestamp": start + (end - start) / 2,
                "text": " ".join(lines[2:]),
            }
        )
    return entries


def extract_caption_contact_sheet(
    video_path: Path,
    captions_path: Path,
    output_dir: Path,
) -> Path:
    """Sample the midpoint of every caption so clipping cannot hide between sparse frames."""
    contact_sheet_path = output_dir / "caption_review_contact_sheet.jpg"
    caption_entries = parse_srt_midpoints(captions_path)
    if not caption_entries:
        raise ValueError(f"No valid captions found in {captions_path}")

    frames: List[Image.Image] = []
    with VideoFileClip(str(video_path)) as clip:
        duration = max(float(clip.duration or 0), 0.1)
        for entry in caption_entries:
            timestamp = min(max(float(entry["timestamp"]), 0.0), max(duration - 0.01, 0.0))
            image = Image.fromarray(clip.get_frame(timestamp)).convert("RGB")
            image.thumbnail((360, 640), Image.Resampling.LANCZOS)

            frame = Image.new("RGB", (360, 682), "white")
            frame.paste(image, ((360 - image.width) // 2, 0))
            draw = ImageDraw.Draw(frame)
            draw.rectangle((0, 640, 360, 682), fill=(0, 0, 0))
            draw.text(
                (8, 648),
                f"caption {entry['index']} | {timestamp:05.1f}s",
                fill=(255, 255, 255),
            )
            frames.append(frame)

    columns = 3
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 360, rows * 682), "white")
    for index, frame in enumerate(frames):
        sheet.paste(frame, ((index % columns) * 360, (index // columns) * 682))

    sheet.save(contact_sheet_path, quality=94)
    return contact_sheet_path


def upload_image_for_review(client: Any, image_path: Path) -> Any:
    uploaded_file = client.files.upload(
        file=str(image_path),
        config=types.UploadFileConfig(mime_type="image/jpeg", display_name=image_path.name),
    )
    return wait_for_uploaded_file(client, uploaded_file)


def build_review_prompt(
    script: Dict[str, Any],
    captions: str,
    video_path: Path,
    visual_basis: str,
    contact_sheet_path: Optional[Path] = None,
    caption_contact_sheet_path: Optional[Path] = None,
    caption_layout: Optional[Dict[str, Any]] = None,
) -> str:
    review_topic = get_review_topic(script, video_path)
    payload = {
        "video_file": str(video_path),
        "visual_review_basis": visual_basis,
        "contact_sheet_file": str(contact_sheet_path) if contact_sheet_path else None,
        "caption_contact_sheet_file": (
            str(caption_contact_sheet_path) if caption_contact_sheet_path else None
        ),
        "caption_layout": caption_layout or {},
        "script": compact_script(script),
        "captions_srt": captions,
        "review_context": {
            "content_type": "short-form parent education video",
            "topic": review_topic,
            "publish_intent": "general education only, not diagnosis or treatment",
        },
    }
    return (
        "You are a conservative clinical safety reviewer for a legitimate, non-sexual pediatric health-education "
        "video about routine infant development, feeding observation, and caregiver safety.\n"
        "Review the uploaded visual evidence together with the authored transcript and captions.\n"
        "Do not rewrite, translate, or localize the script. The authored script is the source of truth.\n"
        "Evaluate whether the final video is medically/developmentally safe to publish for a general audience.\n"
        "You must inspect the uploaded visual evidence. Do not approve the visual checks from keywords alone. "
        "If visual_review_basis is sampled_frames_contact_sheet, the attached image is a time-ordered sample "
        "of frames extracted from the final rendered video; use it to evaluate whether the visible content is "
        "off-topic, alarming, diagnosis-implying, or inconsistent with the narration. "
        "Set actual_video_reviewed to true only if you inspected the attached video or sampled frames.\n"
        "A second attached contact sheet samples the midpoint of every subtitle. Inspect every tile for text "
        "cut off at the bottom or sides, missing glyphs, mojibake, unreadably small text, and line breaks that "
        "split one whitespace-delimited word. Treat any clipped glyph or broken word as needs_revision. "
        "Use caption_layout as deterministic supporting evidence, but confirm readability from rendered pixels.\n"
        "Focus on: unsupported medical or developmental claims, overdiagnosis, missing caution, dangerous advice, "
        "language corruption, caption/audio mismatch, and visuals that imply a stronger or different medical claim "
        "than the narration.\n"
        f"Be strict but practical. The topic is {review_topic}. Flag advice or visuals that overstate certainty, "
        "discourage professional care, or imply diagnosis/treatment beyond the script.\n"
        "Return only JSON with this exact shape:\n"
        "{\n"
        '  "review_type": "final_video_medical_review",\n'
        '  "language": "",\n'
        '  "publish_status": "approved | needs_revision | blocked",\n'
        '  "must_not_publish": false,\n'
        '  "actual_video_reviewed": false,\n'
        '  "visual_review_basis": "",\n'
        '  "medical_safety_score": 5,\n'
        '  "accuracy_score": 5,\n'
        '  "language_integrity_score": 5,\n'
        '  "video_script_match_score": 5,\n'
        '  "caption_render_score": 5,\n'
        '  "observed_visuals_ko": "",\n'
        '  "summary_ko": "",\n'
        '  "source_limitations_ko": "",\n'
        '  "checks": {\n'
        '    "medical_claims_safe": false,\n'
        '    "professional_help_caution_present": false,\n'
        '    "not_diagnostic_or_prescriptive": false,\n'
        '    "captions_readable_and_not_corrupted": false,\n'
        '    "caption_glyphs_not_clipped": false,\n'
        '    "caption_words_not_split": false,\n'
        '    "caption_safe_area_respected": false,\n'
        '    "visuals_do_not_mislead": false\n'
        "  },\n"
        '  "findings": [\n'
        "    {\n"
        '      "severity": "critical | major | minor",\n'
        '      "time_or_block": "",\n'
        '      "issue_ko": "",\n'
        '      "evidence_ko": "",\n'
        '      "recommendation_ko": ""\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Use scores from 1 to 5. Use 5 for publishable with no meaningful medical or rendering issue, "
        "4 for acceptable with small improvements, 3 for revision needed, and 1-2 for blocked or unsafe.\n"
        "If publish_status is approved and all checks are true, every score must be 4 or 5. Never return an "
        "approved result with a score of 1-3.\n"
        "Write summary_ko, source_limitations_ko, and finding fields in Korean.\n\n"
        f"REVIEW_PAYLOAD:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def review_output_dir(output_dir: Path, model: str, visual_input: str) -> Dict[str, Any]:
    video_path = output_dir / "final_video.mp4"
    script_path = output_dir / "script.json"
    captions_path = output_dir / "captions.srt"
    caption_layout_path = output_dir / "caption_layout.json"

    if not video_path.exists():
        raise FileNotFoundError(f"Missing final video: {video_path}")

    client = get_gemini_client()
    script = load_json(script_path)
    captions = load_text(captions_path)
    caption_layout = load_json(caption_layout_path) if caption_layout_path.exists() else {}
    caption_contact_sheet_path = extract_caption_contact_sheet(
        video_path,
        captions_path,
        output_dir,
    )
    uploaded_caption_sheet = upload_image_for_review(client, caption_contact_sheet_path)

    contact_sheet_path = None
    if visual_input == "video":
        uploaded_visual = upload_video_for_review(client, video_path)
        visual_basis = "uploaded_final_video"
    else:
        contact_sheet_path = extract_contact_sheet(video_path, output_dir)
        uploaded_visual = upload_image_for_review(client, contact_sheet_path)
        visual_basis = "sampled_frames_contact_sheet"

    review = gemini_json(
        client,
        model,
        [
            uploaded_visual,
            uploaded_caption_sheet,
            build_review_prompt(
                script,
                captions,
                video_path,
                visual_basis,
                contact_sheet_path,
                caption_contact_sheet_path,
                caption_layout,
            ),
        ],
    )
    review["output_dir"] = str(output_dir)
    review["video_file"] = str(video_path)
    review["contact_sheet_file"] = str(contact_sheet_path) if contact_sheet_path else None
    review["visual_review_basis"] = visual_basis
    review["caption_contact_sheet_file"] = str(caption_contact_sheet_path)
    review["caption_layout_file"] = str(caption_layout_path) if caption_layout_path.exists() else None
    review["script_file"] = str(script_path)
    review["captions_file"] = str(captions_path)
    review["model"] = model
    review["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    review["pipeline_gate_passed"] = final_review_passed(review)
    (output_dir / "final_quality_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return review


def final_review_passed(review: Dict[str, Any]) -> bool:
    """Apply a conservative publication gate to Gemini's final-video response."""
    checks = review.get("checks") or {}
    required_checks = (
        "medical_claims_safe",
        "not_diagnostic_or_prescriptive",
        "captions_readable_and_not_corrupted",
        "caption_glyphs_not_clipped",
        "caption_words_not_split",
        "caption_safe_area_respected",
        "visuals_do_not_mislead",
    )
    scores = (
        review.get("medical_safety_score"),
        review.get("accuracy_score"),
        review.get("language_integrity_score"),
        review.get("video_script_match_score"),
        review.get("caption_render_score"),
    )
    return (
        review.get("publish_status") == "approved"
        and review.get("must_not_publish") is not True
        and review.get("actual_video_reviewed") is True
        and all(isinstance(score, (int, float)) and score >= 4 for score in scores)
        and all(checks.get(check) is True for check in required_checks)
    )


def render_text_report(reviews: List[Dict[str, Any]]) -> str:
    lines = [
        "# Gemini Medical Video Review",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Scope: final rendered MP4 review using Gemini visual evidence, script.json, and captions.srt.",
        "This is an automated publication-safety screen, not a clinician sign-off.",
        "",
    ]

    for review in reviews:
        lines.extend(
            [
                f"## {review.get('language', 'unknown').upper()}",
                "",
                f"- Video: {review.get('video_file', '')}",
                f"- Visual review basis: {review.get('visual_review_basis', '')}",
                f"- Contact sheet: {review.get('contact_sheet_file', '')}",
                f"- Caption contact sheet: {review.get('caption_contact_sheet_file', '')}",
                f"- Status: {review.get('publish_status', '')}",
                f"- Pipeline gate passed: {review.get('pipeline_gate_passed', '')}",
                f"- Must not publish: {review.get('must_not_publish', '')}",
                f"- Actual video reviewed: {review.get('actual_video_reviewed', '')}",
                f"- Medical safety score: {review.get('medical_safety_score', '')}/5",
                f"- Accuracy score: {review.get('accuracy_score', '')}/5",
                f"- Language integrity score: {review.get('language_integrity_score', '')}/5",
                f"- Video/script match score: {review.get('video_script_match_score', '')}/5",
                f"- Caption render score: {review.get('caption_render_score', '')}/5",
                "",
                "Summary:",
                str(review.get("summary_ko", "")),
                "",
                "Observed visuals:",
                str(review.get("observed_visuals_ko", "")),
                "",
                "Checks:",
            ]
        )
        checks = review.get("checks") or {}
        for key, value in checks.items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "Findings:"])
        findings = review.get("findings") or []
        if not findings:
            lines.append("- None")
        for finding in findings:
            lines.extend(
                [
                    f"- Severity: {finding.get('severity', '')}",
                    f"  Time/block: {finding.get('time_or_block', '')}",
                    f"  Issue: {finding.get('issue_ko', '')}",
                    f"  Evidence: {finding.get('evidence_ko', '')}",
                    f"  Recommendation: {finding.get('recommendation_ko', '')}",
                ]
            )
        lines.extend(
            [
                "",
                "Source limitations:",
                str(review.get("source_limitations_ko", "")),
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review final rendered videos with Gemini.")
    parser.add_argument(
        "--output-dir",
        action="append",
        type=Path,
        help="Pipeline output directory containing final_video.mp4, script.json, and captions.srt. Repeatable.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "output" / "bilingual_toddler_speech" / "medical_video_review.txt",
        help="Path for the text report.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=REPO_ROOT / "output" / "bilingual_toddler_speech" / "medical_video_review.json",
        help="Path for the raw JSON review report.",
    )
    parser.add_argument(
        "--visual-input",
        choices=["video", "frames"],
        default="video",
        help="Upload the final MP4 or a sampled frame contact sheet extracted from it.",
    )
    parser.add_argument("--model", default=None, help="Gemini model override.")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Write reports without raising when the final publication gate fails.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse a language's existing passed final_quality_review.json after interruption.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dirs = args.output_dir or DEFAULT_OUTPUTS
    model = get_review_model(args.model)

    reviews = []
    for output_dir in output_dirs:
        prior_review_path = output_dir / "final_quality_review.json"
        if args.resume and prior_review_path.exists():
            prior_review = load_json(prior_review_path)
            if prior_review.get("pipeline_gate_passed") is True:
                prior_review["visual_review_basis"] = (
                    "sampled_frames_contact_sheet"
                    if prior_review.get("contact_sheet_file")
                    else "uploaded_final_video"
                )
                prior_review_path.write_text(
                    json.dumps(prior_review, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"Resume: existing final review passed: {output_dir}")
                reviews.append(prior_review)
                continue
        print(f"Gemini final video medical review: {output_dir}")
        reviews.append(review_output_dir(output_dir, model, args.visual_input))

    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_text_report(reviews), encoding="utf-8")

    print(f"Created {args.json_report}")
    print(f"Created {args.report}")

    failed_reviews = [review for review in reviews if not review.get("pipeline_gate_passed")]
    if failed_reviews and not args.allow_failures:
        failed_languages = ", ".join(str(review.get("language", "unknown")) for review in failed_reviews)
        raise RuntimeError(f"Final Gemini publication gate failed for: {failed_languages}")


if __name__ == "__main__":
    main()
