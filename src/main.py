"""Run the Korean-first short-form video pipeline."""

from generate_captions import generate_captions
from generate_script import generate_script
from generate_tts import generate_tts
from render_video import main as render_video_main


def main() -> None:
    """Generate script, TTS, captions, and the final video in sequence."""
    generate_script()
    generate_tts()
    generate_captions()
    render_video_main()


if __name__ == "__main__":
    main()
