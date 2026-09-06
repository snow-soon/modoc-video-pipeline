"""Resume Gemini-driven replacement for visual blocks that still fail quality review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from main import repair_failed_visuals  # noqa: E402
from pipeline_paths import build_pipeline_paths  # noqa: E402
from validate_visuals import get_min_visual_score, visual_review_failed  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume automatic replacement of failed visuals.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--video-mode", choices=("metadata", "video"), default="video")
    parser.add_argument("--min-score", type=int, default=None)
    parser.add_argument("--max-replacements", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = build_pipeline_paths(args.input, args.output)
    if not paths.quality_review_file.exists():
        raise FileNotFoundError(f"Missing quality review: {paths.quality_review_file}")

    report = json.loads(paths.quality_review_file.read_text(encoding="utf-8"))
    reviews = report.get("visual_reviews") or []
    min_score = get_min_visual_score(args.min_score)
    reviews = repair_failed_visuals(
        paths=paths,
        reviews=reviews,
        model=args.model,
        mode=args.video_mode,
        min_score=min_score,
        max_replacements=args.max_replacements,
    )

    failed = [review for review in reviews if visual_review_failed(review, min_score)]
    if failed:
        failed_ids = ", ".join(review.get("block_id", "") for review in failed)
        raise RuntimeError(f"Visual repair still failed for: {failed_ids}")
    print("All visual blocks passed Gemini review.")


if __name__ == "__main__":
    main()
