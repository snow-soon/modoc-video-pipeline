"""Run the human-script-first short-form video pipeline."""

from generate_captions import generate_captions
from download_assets import download_assets
from generate_script import generate_script
from generate_tts import generate_tts
from render_video import render_video
from search_assets import search_assets


def main() -> None:
    """Run the full pipeline end to end."""
    print("Pipeline started")

    print("Step: generate_script")
    generate_script()
    print("Step completed: generate_script")

    print("Step: generate_tts")
    generate_tts()
    print("Step completed: generate_tts")

    print("Step: generate_captions")
    generate_captions()
    print("Step completed: generate_captions")

    print("Step: search_assets")
    search_assets()
    print("Step completed: search_assets")

    print("Step: download_assets")
    download_assets()
    print("Step completed: download_assets")

    print("Step: render_video")
    final_video_path = render_video()
    print("Step completed: render_video")

    print("Pipeline finished")
    print(f"Final video path: {final_video_path}")


if __name__ == "__main__":
    main()
