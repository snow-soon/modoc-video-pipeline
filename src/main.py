"""Run the authored multi-language video pipeline."""

from __future__ import annotations

import argparse

from download_assets import download_assets
from generate_captions import generate_captions
from generate_script import generate_script
from generate_tts import generate_tts
from pipeline_paths import build_pipeline_paths
from search_assets import search_assets
from validate_visuals import review_script_quality, review_visual_quality


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
        "--allow-quality-failures",
        action="store_true",
        help="Continue rendering even if Gemini quality review fails.",
    )
    return parser.parse_args()


def main() -> None:
    """Generate one full language-specific video output in sequence."""
    args = parse_args()
    paths = build_pipeline_paths(args.input, args.output)
    quality_review_enabled = not args.skip_quality_review
    total_steps = 8 if quality_review_enabled else 6
    fail_on_quality = not args.allow_quality_failures
    step = 1

    print(f"[{step}/{total_steps}] Normalize script plan: {paths.input_file}")
    generate_script(paths)
    step += 1

    if quality_review_enabled:
        print(f"[{step}/{total_steps}] Gemini medical script review")
        review_script_quality(
            paths,
            model=args.review_model,
            fail_on_blocked=fail_on_quality,
        )
        step += 1

    print(f"[{step}/{total_steps}] Generate TTS audio")
    generate_tts(paths)
    step += 1

    print(f"[{step}/{total_steps}] Generate captions and timing plan")
    generate_captions(paths)
    step += 1

    print(f"[{step}/{total_steps}] Search stock assets")
    search_assets(paths)
    step += 1

    print(f"[{step}/{total_steps}] Download stock assets")
    download_assets(paths)
    step += 1

    if quality_review_enabled:
        print(f"[{step}/{total_steps}] Gemini visual match review")
        review_visual_quality(
            paths,
            model=args.review_model,
            mode=args.review_video_mode,
            min_score=args.review_min_score,
            fail_on_blocked=fail_on_quality,
        )
        step += 1

    print(f"[{step}/{total_steps}] Render final video")
    from render_video import render_video as render_video_impl
    from render_video import load_captions, load_script, load_timing_plan

    try:
        from moviepy import AudioFileClip
    except ImportError:
        from moviepy.editor import AudioFileClip  # type: ignore

    script = load_script(paths)
    timing_plan = load_timing_plan(paths)
    captions = load_captions(paths)
    audio_clip = AudioFileClip(str(paths.audio_file))
    final_video = render_video_impl(paths, script, timing_plan, audio_clip, captions)
    final_video.write_videofile(
        str(paths.final_video_file),
        fps=30,
        codec="libx264",
        audio_codec="aac",
    )
    final_video.close()
    audio_clip.close()
    print(f"Created {paths.final_video_file}")


if __name__ == "__main__":
    main()
