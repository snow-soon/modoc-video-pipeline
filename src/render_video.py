"""Render a short-form vertical video using downloaded stock clips."""

import json
from pathlib import Path

try:
    # MoviePy 2.x style imports.
    from moviepy import (
        AudioFileClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )
except ImportError:
    # Fallback for older MoviePy versions.
    from moviepy.editor import (  # type: ignore
        AudioFileClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPT_FILE = BASE_DIR / "output" / "script.json"
AUDIO_FILE = BASE_DIR / "output" / "narration.wav"
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_FILE = BASE_DIR / "output" / "final_video.mp4"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_SIZE = (VIDEO_WIDTH, VIDEO_HEIGHT)
FPS = 30
TEXT_COLOR = "white"
TEXT_BOX_WIDTH = 920
FONT_SIZE = 72
BOTTOM_MARGIN = 220


def set_clip_position(clip, position):
    """Support both new and old MoviePy position methods."""
    if hasattr(clip, "with_position"):
        return clip.with_position(position)
    return clip.set_position(position)


def set_clip_duration(clip, duration: float):
    """Support both new and old MoviePy duration methods."""
    if hasattr(clip, "with_duration"):
        return clip.with_duration(duration)
    return clip.set_duration(duration)


def set_clip_audio(clip, audio_clip):
    """Support both new and old MoviePy audio methods."""
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio_clip)
    return clip.set_audio(audio_clip)


def remove_clip_audio(clip):
    """Remove the original background clip audio."""
    if hasattr(clip, "without_audio"):
        return clip.without_audio()
    return clip.set_audio(None)


def resize_clip(clip, width=None, height=None):
    """Resize a clip for both MoviePy API styles."""
    if hasattr(clip, "resized"):
        return clip.resized(width=width, height=height)
    return clip.resize(width=width, height=height)


def crop_clip(clip, x1: float, y1: float, x2: float, y2: float):
    """Crop a clip for both MoviePy API styles."""
    if hasattr(clip, "cropped"):
        return clip.cropped(x1=x1, y1=y1, x2=x2, y2=y2)
    return clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)


def load_script() -> dict:
    """Read the generated script JSON file."""
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Missing script file: {SCRIPT_FILE}")

    with SCRIPT_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_scene_video_path(scene_index: int) -> Path:
    """Return the downloaded video path for a scene."""
    video_path = ASSETS_DIR / f"scene_{scene_index}.mp4"

    if not video_path.exists():
        raise FileNotFoundError(f"Missing scene video: {video_path}")

    return video_path


def make_text_clip(caption: str, duration: float) -> TextClip:
    """Create the caption shown near the bottom of the screen."""
    font_candidates = ["Arial-BoldMT", "Arial-Bold", "Helvetica-Bold", None]
    last_error = None

    for font_name in font_candidates:
        try:
            return TextClip(
                text=caption,
                font=font_name,
                font_size=FONT_SIZE,
                color=TEXT_COLOR,
                size=(TEXT_BOX_WIDTH, None),
                method="caption",
                text_align="center",
                duration=duration,
            )
        except Exception as error:
            last_error = error

        try:
            clip = TextClip(
                txt=caption,
                font=font_name,
                fontsize=FONT_SIZE,
                color=TEXT_COLOR,
                size=(TEXT_BOX_WIDTH, None),
                method="caption",
                align="center",
            )
            return set_clip_duration(clip, duration)
        except Exception as error:
            last_error = error

    raise RuntimeError(f"Could not create text clip: {last_error}")


def loop_clip_to_duration(clip, duration: float):
    """Repeat a clip until it is long enough, then trim to the exact duration."""
    if clip.duration is None or clip.duration <= 0:
        raise ValueError("Background video has no valid duration.")

    looped_clips = []
    total_duration = 0.0

    while total_duration < duration:
        looped_clips.append(clip.copy())
        total_duration += clip.duration

    looped_clip = concatenate_videoclips(looped_clips, method="compose")

    if hasattr(looped_clip, "subclipped"):
        return looped_clip.subclipped(0, duration)
    return looped_clip.subclip(0, duration)


def fit_clip_to_vertical_frame(clip):
    """Resize and crop a clip so it fills a 1080x1920 vertical frame."""
    # First make the clip tall enough for 1920px height.
    resized_clip = resize_clip(clip, height=VIDEO_HEIGHT)

    # Then crop equally from the left and right if it is wider than 1080px.
    extra_width = max(resized_clip.w - VIDEO_WIDTH, 0)
    crop_left = extra_width / 2
    crop_right = crop_left + VIDEO_WIDTH

    return crop_clip(
        resized_clip,
        x1=crop_left,
        y1=0,
        x2=crop_right,
        y2=VIDEO_HEIGHT,
    )


def make_scene_clip(scene: dict, scene_index: int):
    """Build one scene using the downloaded stock video as background."""
    duration = float(scene["end"]) - float(scene["start"])

    if duration <= 0:
        raise ValueError(f"Scene has invalid duration: {scene}")

    background_path = get_scene_video_path(scene_index)

    # Load the downloaded stock video for this scene.
    background_clip = VideoFileClip(str(background_path))

    # Remove the clip's original sound because narration will be added later.
    background_clip = remove_clip_audio(background_clip)

    # Repeat the background if it is shorter than the scene timing.
    background_clip = loop_clip_to_duration(background_clip, duration)

    # Resize and crop the clip so it fills a vertical 9:16 frame.
    background_clip = fit_clip_to_vertical_frame(background_clip)
    background_clip = set_clip_duration(background_clip, duration)

    # Create the caption and place it near the bottom center.
    caption_clip = make_text_clip(scene["caption"], duration)
    caption_position = ("center", VIDEO_HEIGHT - BOTTOM_MARGIN)
    caption_clip = set_clip_position(caption_clip, caption_position)

    # Combine the background and caption into one scene clip.
    return CompositeVideoClip([background_clip, caption_clip], size=VIDEO_SIZE)


def build_video(script: dict):
    """Create the final video by joining all scene clips together."""
    scenes = script.get("scenes", [])

    if not scenes:
        raise ValueError("script.json does not contain any scenes.")

    scene_clips = [
        make_scene_clip(scene, scene_index)
        for scene_index, scene in enumerate(scenes, start=1)
    ]
    return concatenate_videoclips(scene_clips, method="compose")


def main() -> None:
    """Render the final MP4 using downloaded videos and narration audio."""
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Missing narration file: {AUDIO_FILE}")

    # Read the scene timings and captions from the script file.
    script = load_script()

    # Build the full video from the downloaded scene clips.
    video = build_video(script)

    # Add the generated narration as the final audio track.
    audio = AudioFileClip(str(AUDIO_FILE))
    final_video = set_clip_audio(video, audio)

    # Export the completed short-form video.
    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
    )

    final_video.close()
    video.close()
    audio.close()

    print(f"Created {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
