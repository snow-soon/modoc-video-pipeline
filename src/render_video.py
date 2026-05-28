"""Render a Korean-first vertical video synchronized to narration and SRT captions."""

import json
from pathlib import Path

try:
    # MoviePy 2.x style imports.
    from moviepy import (
        AudioFileClip,
        ColorClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )
except ImportError:
    # Fallback for older MoviePy versions.
    from moviepy.editor import (  # type: ignore
        AudioFileClip,
        ColorClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPT_FILE = BASE_DIR / "output" / "script.json"
AUDIO_FILE = BASE_DIR / "output" / "narration.wav"
CAPTIONS_FILE = BASE_DIR / "output" / "captions.srt"
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_FILE = BASE_DIR / "output" / "final_video.mp4"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_SIZE = (VIDEO_WIDTH, VIDEO_HEIGHT)
FPS = 30

BACKGROUND_COLOR = (18, 18, 18)
CAPTION_COLOR = "white"
CAPTION_STROKE_COLOR = "black"
CAPTION_STROKE_WIDTH = 3
CAPTION_BOX_WIDTH = 900
CAPTION_MAX_HEIGHT = 320
CAPTION_TOP_RATIO = 0.70
CAPTION_SIDE_MARGIN = 90
CAPTION_BOTTOM_MARGIN = 140
FONT_SIZES = [74, 70, 66, 62, 58, 54, 50]
KOREAN_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


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


def set_clip_start(clip, start_time: float):
    """Support both new and old MoviePy start-time methods."""
    if hasattr(clip, "with_start"):
        return clip.with_start(start_time)
    return clip.set_start(start_time)


def set_clip_opacity(clip, opacity: float):
    """Support both new and old MoviePy opacity methods."""
    if hasattr(clip, "with_opacity"):
        return clip.with_opacity(opacity)
    return clip.set_opacity(opacity)


def remove_clip_audio(clip):
    """Remove audio from a stock video clip."""
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


def trim_clip(clip, start_time: float, end_time: float):
    """Trim a clip for both new and old MoviePy APIs."""
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start_time, end_time)
    return clip.subclip(start_time, end_time)


def load_script() -> dict:
    """Read the generated script JSON file."""
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Missing script file: {SCRIPT_FILE}")

    with SCRIPT_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_available_korean_fonts() -> list[str]:
    """Return Korean font file paths that exist on this machine."""
    return [font_path for font_path in KOREAN_FONT_CANDIDATES if Path(font_path).exists()]


def load_captions() -> list[dict]:
    """Parse SRT captions into a list of timed subtitle entries."""
    if not CAPTIONS_FILE.exists():
        raise FileNotFoundError(f"Missing captions file: {CAPTIONS_FILE}")

    raw_text = CAPTIONS_FILE.read_text(encoding="utf-8").strip()

    if not raw_text:
        return []

    blocks = raw_text.split("\n\n")
    captions = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue

        start_text, end_text = lines[1].split(" --> ")
        captions.append(
            {
                "start": srt_timestamp_to_seconds(start_text),
                "end": srt_timestamp_to_seconds(end_text),
                "text": "\n".join(lines[2:]),
            }
        )

    return captions


def srt_timestamp_to_seconds(timestamp: str) -> float:
    """Convert an SRT timestamp into seconds."""
    hours_part, minutes_part, seconds_part = timestamp.split(":")
    seconds_text, milliseconds_text = seconds_part.split(",")

    hours = int(hours_part)
    minutes = int(minutes_part)
    seconds = int(seconds_text)
    milliseconds = int(milliseconds_text)

    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def calculate_scene_timings(audio_duration: float, scenes: list) -> list:
    """Split the full audio duration evenly across the available scenes."""
    if not scenes:
        raise ValueError("script.json does not contain any scenes.")

    equal_duration = audio_duration / len(scenes)
    adjusted_scenes = []
    current_start = 0.0

    for index, _scene in enumerate(scenes, start=1):
        if index == len(scenes):
            current_end = audio_duration
        else:
            current_end = current_start + equal_duration

        adjusted_scenes.append(
            {
                "index": index,
                "start": current_start,
                "end": current_end,
                "duration": current_end - current_start,
            }
        )
        current_start = current_end

    return adjusted_scenes


def get_scene_video_path(scene_index: int) -> Path:
    """Return the expected stock video path for a scene."""
    return ASSETS_DIR / f"scene_{scene_index}.mp4"


def make_fallback_background(duration: float):
    """Create a simple dark fallback clip when a stock video is missing."""
    return ColorClip(size=VIDEO_SIZE, color=BACKGROUND_COLOR, duration=duration)


def loop_or_trim_clip(clip, duration: float):
    """Make sure a background clip matches the exact scene duration."""
    if clip.duration is None or clip.duration <= 0:
        raise ValueError("Background video has no valid duration.")

    if clip.duration >= duration:
        return trim_clip(clip, 0, duration)

    looped_clips = []
    total_duration = 0.0

    while total_duration < duration:
        looped_clips.append(clip.copy())
        total_duration += clip.duration

    combined_clip = concatenate_videoclips(looped_clips, method="compose")
    return trim_clip(combined_clip, 0, duration)


def fit_clip_to_vertical_frame(clip):
    """Resize and crop a clip to fill the 1080x1920 frame."""
    width_scale = VIDEO_WIDTH / clip.w
    height_scale = VIDEO_HEIGHT / clip.h
    scale = max(width_scale, height_scale)

    resized_width = int(round(clip.w * scale))
    resized_height = int(round(clip.h * scale))
    resized_clip = resize_clip(clip, width=resized_width, height=resized_height)

    x1 = max((resized_clip.w - VIDEO_WIDTH) / 2, 0)
    y1 = max((resized_clip.h - VIDEO_HEIGHT) / 2, 0)
    x2 = x1 + VIDEO_WIDTH
    y2 = y1 + VIDEO_HEIGHT

    return crop_clip(resized_clip, x1=x1, y1=y1, x2=x2, y2=y2)


def make_background_clip(scene_index: int, duration: float):
    """Create the background clip for one scene."""
    video_path = get_scene_video_path(scene_index)

    if not video_path.exists():
        print(f"Scene {scene_index}: asset missing, using dark fallback background")
        return set_clip_duration(make_fallback_background(duration), duration)

    print(f"Scene {scene_index}: using asset {video_path.name}")

    background_clip = VideoFileClip(str(video_path))
    background_clip = remove_clip_audio(background_clip)
    background_clip = loop_or_trim_clip(background_clip, duration)
    background_clip = fit_clip_to_vertical_frame(background_clip)
    return set_clip_duration(background_clip, duration)


def build_text_clip(text: str, duration: float, font_size: int):
    """Create one timed text clip attempt for a specific font size."""
    font_candidates = get_available_korean_fonts() + [None]
    last_error = None

    for font_name in font_candidates:
        try:
            return TextClip(
                text=text,
                font=font_name,
                font_size=font_size,
                color=CAPTION_COLOR,
                stroke_color=CAPTION_STROKE_COLOR,
                stroke_width=CAPTION_STROKE_WIDTH,
                size=(CAPTION_BOX_WIDTH, None),
                method="caption",
                text_align="center",
                duration=duration,
            )
        except Exception as error:
            last_error = error

        try:
            clip = TextClip(
                txt=text,
                font=font_name,
                fontsize=font_size,
                color=CAPTION_COLOR,
                stroke_color=CAPTION_STROKE_COLOR,
                stroke_width=CAPTION_STROKE_WIDTH,
                size=(CAPTION_BOX_WIDTH, None),
                method="caption",
                align="center",
            )
            return set_clip_duration(clip, duration)
        except Exception as error:
            last_error = error

    raise RuntimeError(f"Could not create text clip: {last_error}")


def make_caption_clip(text: str, start_time: float, end_time: float):
    """Create one caption overlay clip from an SRT subtitle entry."""
    duration = max(end_time - start_time, 0.01)
    chosen_text_clip = None

    for font_size in FONT_SIZES:
        text_clip = build_text_clip(text, duration, font_size)

        if text_clip.w <= CAPTION_BOX_WIDTH and text_clip.h <= CAPTION_MAX_HEIGHT:
            chosen_text_clip = text_clip
            break

        if chosen_text_clip is None:
            chosen_text_clip = text_clip

    if chosen_text_clip is None:
        raise RuntimeError("Could not build a caption clip.")

    box_width = min(chosen_text_clip.w + 50, VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2))
    box_height = chosen_text_clip.h + 40
    box_clip = ColorClip(
        size=(int(box_width), int(box_height)),
        color=(0, 0, 0),
        duration=duration,
    )
    box_clip = set_clip_opacity(box_clip, 0.45)

    text_x = (box_width - chosen_text_clip.w) / 2
    text_y = (box_height - chosen_text_clip.h) / 2
    text_clip = set_clip_position(chosen_text_clip, (text_x, text_y))

    caption_group = CompositeVideoClip(
        [box_clip, text_clip],
        size=(int(box_width), int(box_height)),
    )
    caption_group = set_clip_duration(caption_group, duration)

    safe_top = int(VIDEO_HEIGHT * CAPTION_TOP_RATIO)
    max_top = VIDEO_HEIGHT - int(box_height) - CAPTION_BOTTOM_MARGIN
    caption_top = min(safe_top, max_top)
    caption_top = max(caption_top, 80)

    caption_group = set_clip_position(caption_group, ("center", caption_top))
    return set_clip_start(caption_group, start_time)


def make_scene_clip(scene: dict):
    """Build one background scene clip."""
    background_clip = make_background_clip(scene["index"], scene["duration"])
    return set_clip_duration(background_clip, scene["duration"])


def build_background_video(audio_duration: float, scenes: list):
    """Build the full background track from evenly timed scene clips."""
    adjusted_scenes = calculate_scene_timings(audio_duration, scenes)

    print(f"Audio duration: {audio_duration:.2f} seconds")
    print(f"Number of scenes: {len(adjusted_scenes)}")
    print(f"Adjusted duration per scene: {audio_duration / len(adjusted_scenes):.2f} seconds")

    for scene in adjusted_scenes:
        print(
            f"Scene {scene['index']}: "
            f"start={scene['start']:.2f}s, "
            f"end={scene['end']:.2f}s, "
            f"duration={scene['duration']:.2f}s"
        )

    scene_clips = [make_scene_clip(scene) for scene in adjusted_scenes]
    video = concatenate_videoclips(scene_clips, method="compose")
    return set_clip_duration(video, audio_duration)


def build_caption_overlays(captions: list[dict]) -> list:
    """Build timed caption overlay clips from SRT entries."""
    overlays = []

    for caption in captions:
        overlays.append(
            make_caption_clip(
                text=caption["text"],
                start_time=caption["start"],
                end_time=caption["end"],
            )
        )

    return overlays


def render_video(script: dict, audio_clip, captions: list[dict]):
    """Render the final video using background scenes and SRT caption overlays."""
    scenes = script.get("scenes", [])
    background_video = build_background_video(audio_clip.duration, scenes)
    caption_overlays = build_caption_overlays(captions)
    available_fonts = get_available_korean_fonts()

    final_layers = [background_video] + caption_overlays
    final_video = CompositeVideoClip(final_layers, size=VIDEO_SIZE)
    final_video = set_clip_duration(final_video, audio_clip.duration)
    final_video = set_clip_audio(final_video, audio_clip)

    if available_fonts:
        print(f"Korean caption font: {available_fonts[0]}")
    else:
        print("Korean caption font: no known Korean font file found, using MoviePy default")
    print(f"Loaded {len(captions)} caption entries from captions.srt")
    print(f"Final video duration: {final_video.duration:.2f} seconds")
    return final_video


def main() -> None:
    """Render the final MP4 from script scenes, narration, and timed SRT captions."""
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Missing narration file: {AUDIO_FILE}")

    script = load_script()
    captions = load_captions()
    audio_clip = AudioFileClip(str(AUDIO_FILE))

    final_video = render_video(script, audio_clip, captions)

    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
    )

    final_video.close()
    audio_clip.close()

    print(f"Created {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
