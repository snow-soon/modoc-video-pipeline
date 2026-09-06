"""Run the authored multi-language video pipeline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from download_assets import download_assets
from generate_captions import generate_captions
from generate_script import generate_script
from generate_tts import generate_tts
from optimize_assets import optimize_assets
from pipeline_paths import build_pipeline_paths
from pipeline_state import StageCache, file_digest
from search_assets import search_assets
from validate_visuals import (
    get_min_visual_score,
    review_script_quality,
    review_visual_quality,
    visual_review_failed,
    script_review_current,
    get_review_model,
)


DEFAULT_MAX_VISUAL_REPLACEMENTS = 3


def get_max_visual_replacements(value: Optional[int]) -> int:
    """Resolve the bounded number of automatic stock-footage replacement rounds."""
    if value is not None:
        return max(0, min(value, 6))
    raw_value = os.getenv("GEMINI_MAX_VISUAL_REPLACEMENTS", str(DEFAULT_MAX_VISUAL_REPLACEMENTS))
    try:
        return max(0, min(int(raw_value), 6))
    except ValueError:
        return DEFAULT_MAX_VISUAL_REPLACEMENTS


def load_asset_map(paths) -> Dict[str, dict]:
    """Load selected asset metadata keyed by block ID."""
    if not paths.assets_file.exists():
        return {}
    assets = json.loads(paths.assets_file.read_text(encoding="utf-8"))
    return {asset.get("block_id", ""): asset for asset in assets}


def load_rejected_asset_history(paths) -> List[dict]:
    """Load prior rejected asset metadata so reruns do not select it again."""
    if not paths.rejected_assets_file.exists():
        return []
    return json.loads(paths.rejected_assets_file.read_text(encoding="utf-8"))


def save_rejected_asset_history(paths, history: List[dict]) -> None:
    """Persist rejected selections and Gemini evidence for audit and retry exclusion."""
    paths.rejected_assets_file.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def archive_rejected_asset(paths, block_id: str, round_number: int) -> None:
    """Preserve a rejected clip for audit while freeing the canonical block path."""
    source = paths.assets_dir / f"{block_id}.mp4"
    if not source.exists():
        return

    destination = paths.assets_dir / f"{block_id}.rejected-round-{round_number}.mp4"
    suffix = 2
    while destination.exists():
        destination = paths.assets_dir / f"{block_id}.rejected-round-{round_number}-{suffix}.mp4"
        suffix += 1
    source.rename(destination)
    print(f"Archived rejected asset: {destination}")


def reuse_assets(source_output: Path, paths) -> None:
    """Copy a previously validated visual set into another language run."""
    source_assets_file = source_output / "assets.json"
    source_assets_dir = source_output / "assets"
    if not source_assets_file.exists() or not source_assets_dir.exists():
        raise FileNotFoundError(f"Reusable assets are incomplete: {source_output}")

    paths.ensure_directories()
    shutil.copy2(source_assets_file, paths.assets_file)
    for asset in json.loads(source_assets_file.read_text(encoding="utf-8")):
        block_id = asset.get("block_id", "")
        source_video = source_assets_dir / f"{block_id}.mp4"
        if not source_video.exists():
            raise FileNotFoundError(f"Missing reusable video: {source_video}")
        shutil.copy2(source_video, paths.assets_dir / source_video.name)
    synchronize_asset_metadata(paths)
    print(f"Reused validated stock assets from {source_output}")


def synchronize_asset_metadata(paths) -> None:
    """Replace source-language narrative fields with the target run's authored fields."""
    script = json.loads(paths.script_file.read_text(encoding="utf-8"))
    assets = json.loads(paths.assets_file.read_text(encoding="utf-8"))
    block_map = {block.get("id", ""): block for block in script.get("blocks", [])}
    for asset in assets:
        block = block_map.get(asset.get("block_id", ""))
        if not block:
            continue
        for field in ("narration", "captions", "visual_keywords", "avoid_visuals"):
            asset[field] = block.get(field, [] if field != "narration" else "")
    paths.assets_file.write_text(
        json.dumps(assets, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def script_review_passed(paths, model: Optional[str] = None) -> bool:
    """Return whether a prior script quality gate completed successfully."""
    return script_review_current(paths, model)


def canonical_assets_exist(paths) -> bool:
    """Return whether every authored block has a canonical MP4 and metadata."""
    if not paths.script_file.exists() or not paths.assets_file.exists():
        return False
    script = json.loads(paths.script_file.read_text(encoding="utf-8"))
    assets = load_asset_map(paths)
    return bool(script.get("blocks")) and all(
        block.get("id") in assets and (paths.assets_dir / f"{block.get('id')}.mp4").is_file()
        for block in script.get("blocks", [])
    )


def blocks_requiring_search(paths) -> List[str]:
    """Retain selected metadata after interruption; missing files can be downloaded again."""
    script = json.loads(paths.script_file.read_text(encoding="utf-8"))
    assets = load_asset_map(paths)
    return [block["id"] for block in script["blocks"]
            if not (assets.get(block["id"]) or {}).get("download_url")]


def review_rendered_output(paths, model: Optional[str], allow_failures: bool = False) -> None:
    """Single-language runs must also review the actual rendered audio and pixels."""
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "review_final_video_medical.py"),
               "--output-dir", str(paths.output_dir), "--visual-input", "video", "--resume",
               "--report", str(paths.output_dir / "medical_video_review.txt"),
               "--json-report", str(paths.output_dir / "medical_video_review.json")]
    if model:
        command.extend(["--model", model])
    if allow_failures:
        command.append("--allow-failures")
    subprocess.run(command, check=True)


def repair_failed_visuals(
    paths,
    reviews: List[dict],
    model: Optional[str],
    mode: Optional[str],
    min_score: int,
    max_replacements: int,
) -> List[dict]:
    """Replace failed stock clips and re-run Gemini until all pass or retries end."""
    rejected_history = load_rejected_asset_history(paths)
    excluded_video_ids: Set[str] = {
        str(entry.get("asset", {}).get("pexels_video_id"))
        for entry in rejected_history
        if entry.get("asset", {}).get("pexels_video_id") is not None
    }

    for round_number in range(1, max_replacements + 1):
        failed_reviews = [review for review in reviews if visual_review_failed(review, min_score)]
        if not failed_reviews:
            return reviews

        failed_ids = [review.get("block_id", "") for review in failed_reviews]
        asset_map = load_asset_map(paths)
        keyword_overrides = {}
        for review in failed_reviews:
            block_id = review.get("block_id", "")
            asset = asset_map.get(block_id) or {}
            video_id = asset.get("pexels_video_id")
            if video_id is not None:
                excluded_video_ids.add(str(video_id))
            rejected_history.append(
                {
                    "round": round_number,
                    "block_id": block_id,
                    "asset": asset,
                    "review": review,
                }
            )
            suggested = [
                keyword.strip()
                for keyword in review.get("suggested_keywords") or []
                if isinstance(keyword, str) and keyword.strip()
            ]
            keyword_overrides[block_id] = suggested
            archive_rejected_asset(paths, block_id, round_number)

        save_rejected_asset_history(paths, rejected_history)

        print(
            f"Gemini visual replacement round {round_number}/{max_replacements}: "
            f"{', '.join(failed_ids)}"
        )
        search_assets(
            paths,
            block_ids=failed_ids,
            keyword_overrides=keyword_overrides,
            excluded_video_ids=excluded_video_ids,
            preserve_existing=True,
            use_gemini_ranking=True,
            review_model=model,
        )
        download_assets(paths, block_ids=failed_ids)
        optimize_assets(paths)
        reviews = review_visual_quality(
            paths,
            model=model,
            mode=mode,
            min_score=min_score,
            fail_on_blocked=False,
            block_ids=failed_ids,
            reuse_passed=False,
        )

    return reviews


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run the full video pipeline for one script plan.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    parser.add_argument(
        "--skip-quality-review",
        action="store_true",
        help="Skip Gemini medical and visual quality gates.",
    )
    parser.add_argument(
        "--review-video-mode",
        choices=["metadata", "video"],
        default=None,
        help="Use selected asset metadata or upload downloaded videos for Gemini visual review.",
    )
    parser.add_argument("--review-model", default=None, help="Gemini quality review model override.")
    parser.add_argument("--review-min-score", type=int, default=None, help="Minimum accepted visual score.")
    parser.add_argument(
        "--max-script-revisions",
        type=int,
        default=None,
        help="Maximum Gemini medical/language script correction rounds.",
    )
    parser.add_argument(
        "--max-visual-replacements",
        type=int,
        default=None,
        help="Maximum failed stock-footage replacement rounds.",
    )
    parser.add_argument(
        "--reuse-assets-from",
        type=Path,
        default=None,
        help="Reuse the approved assets.json and block MP4s from another language output.",
    )
    parser.add_argument(
        "--allow-quality-failures",
        action="store_true",
        help="Continue rendering even if Gemini quality review fails.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed script, TTS, caption, and asset stages after interruption.",
    )
    return parser.parse_args()


def main() -> None:
    """Generate one full language-specific video output in sequence."""
    args = parse_args()
    paths = build_pipeline_paths(args.input, args.output)
    cache = StageCache(paths.output_dir)
    script_inputs = {"authored": file_digest(paths.input_file),
                     "normalizer": file_digest(Path(__file__).with_name("generate_script.py")),
                     "review_policy": file_digest(Path(__file__).with_name("validate_visuals.py")),
                     "source_policy": file_digest(Path(__file__).with_name("medical_evidence.py")),
                     "review_model": None if args.skip_quality_review else get_review_model(args.review_model)}
    script_outputs = [paths.script_file, paths.narration_text_file]
    quality_review_enabled = not args.skip_quality_review
    total_steps = 9 if quality_review_enabled else 6
    fail_on_quality = not args.allow_quality_failures
    step = 1

    print(f"[{step}/{total_steps}] Normalize script plan: {paths.input_file}")
    if args.resume and cache.matches("script", script_inputs, script_outputs):
        print("Resume: existing normalized script is complete")
    else:
        generate_script(paths)
        cache.record("script", script_inputs, script_outputs)
    step += 1

    if quality_review_enabled:
        print(f"[{step}/{total_steps}] Gemini medical script review")
        if args.resume and script_review_passed(paths, args.review_model):
            print("Resume: existing Gemini script review passed")
        else:
            review_script_quality(
                paths,
                model=args.review_model,
                fail_on_blocked=fail_on_quality,
                auto_revise=True,
                max_revisions=args.max_script_revisions,
            )
        cache.record("script", script_inputs, script_outputs)
        step += 1

    print(f"[{step}/{total_steps}] Generate TTS audio")
    generate_tts(paths, resume=args.resume)
    step += 1

    print(f"[{step}/{total_steps}] Generate captions and timing plan")
    generate_captions(paths)
    step += 1

    if args.reuse_assets_from:
        print(f"[{step}/{total_steps}] Reuse stock assets")
        if args.resume and canonical_assets_exist(paths):
            synchronize_asset_metadata(paths)
            print("Resume: existing canonical assets are complete")
        else:
            reuse_assets(args.reuse_assets_from.expanduser().resolve(), paths)
        step += 2
    else:
        print(f"[{step}/{total_steps}] Search stock assets")
        if args.resume and canonical_assets_exist(paths):
            synchronize_asset_metadata(paths)
            print("Resume: selected assets retained; current script and video bytes will be revalidated")
        else:
            pending = blocks_requiring_search(paths) if args.resume else None
            if pending is None or pending:
                rejected_ids = {str(entry["asset"]["pexels_video_id"])
                                for entry in load_rejected_asset_history(paths)
                                if (entry.get("asset") or {}).get("pexels_video_id") is not None}
                search_assets(
                    paths, block_ids=pending, excluded_video_ids=rejected_ids,
                    use_gemini_ranking=quality_review_enabled, review_model=args.review_model,
                )
            else:
                print("Resume: all selections retained; downloading missing files only")
            synchronize_asset_metadata(paths)
        step += 1

        print(f"[{step}/{total_steps}] Download stock assets")
        download_assets(paths)
        step += 1

    optimize_assets(paths)
    if quality_review_enabled:
        print(f"[{step}/{total_steps}] Gemini visual match review")
        resolved_min_score = get_min_visual_score(args.review_min_score)
        visual_reviews = review_visual_quality(
            paths,
            model=args.review_model,
            mode=args.review_video_mode,
            min_score=resolved_min_score,
            fail_on_blocked=False,
        )
        visual_reviews = repair_failed_visuals(
            paths=paths,
            reviews=visual_reviews,
            model=args.review_model,
            mode=args.review_video_mode,
            min_score=resolved_min_score,
            max_replacements=get_max_visual_replacements(args.max_visual_replacements),
        )
        failed_reviews = [
            review for review in visual_reviews if visual_review_failed(review, resolved_min_score)
        ]
        if fail_on_quality and failed_reviews:
            failed_ids = ", ".join(review.get("block_id", "") for review in failed_reviews)
            raise RuntimeError(
                f"Gemini visual review still failed after replacements: {failed_ids}. "
                "See quality_review.json."
            )
        step += 1

    print(f"[{step}/{total_steps}] Render final video")
    from render_ffmpeg import render_final_video
    render_final_video(paths, resume=args.resume)
    if quality_review_enabled:
        print(f"[{total_steps}/{total_steps}] Gemini final rendered video and audio review")
        review_rendered_output(paths, args.review_model, args.allow_quality_failures)


if __name__ == "__main__":
    main()
