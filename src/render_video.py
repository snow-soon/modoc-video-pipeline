"""Render a vertical video synchronized to narration, captions, and block timings."""

import json
import argparse
import re
import unicodedata
from pathlib import Path
from typing import Optional

from PIL import ImageFont

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

from pipeline_paths import PipelinePaths, build_pipeline_paths

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_SIZE = (VIDEO_WIDTH, VIDEO_HEIGHT)
FPS = 30

BACKGROUND_COLOR = (18, 18, 18)
CAPTION_COLOR = "white"
CAPTION_STROKE_COLOR = "black"
CAPTION_STROKE_WIDTH = 3
CAPTION_SIDE_MARGIN = 36
CAPTION_HORIZONTAL_PADDING = 36
CAPTION_VERTICAL_PADDING = 28
CAPTION_BOX_WIDTH = VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2)
CAPTION_TEXT_WIDTH = CAPTION_BOX_WIDTH - (CAPTION_HORIZONTAL_PADDING * 2)
CAPTION_MAX_HEIGHT = 300
CAPTION_TOP_RATIO = 0.62
CAPTION_BOTTOM_MARGIN = 280
CAPTION_INTERLINE = 10
FONT_SIZES = [74, 70, 66, 62, 58, 54, 50, 46, 42]
KOREAN_CAPTION_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
LATIN_CAPTION_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def load_script(paths: PipelinePaths) -> dict:
    """Read the generated script JSON file."""
    if not paths.script_file.exists():
        raise FileNotFoundError(f"Missing script file: {paths.script_file}")

    with paths.script_file.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_available_caption_fonts(language: str = "") -> list[str]:
    """Return caption font file paths in language-aware preference order."""
    normalized_language = (language or "").lower()
    if normalized_language.startswith("ko"):
        candidates = KOREAN_CAPTION_FONT_CANDIDATES + LATIN_CAPTION_FONT_CANDIDATES
    else:
        candidates = LATIN_CAPTION_FONT_CANDIDATES + KOREAN_CAPTION_FONT_CANDIDATES

    seen = set()
    available_fonts = []
    for font_path in candidates:
        if font_path not in seen and Path(font_path).exists():
            seen.add(font_path)
            available_fonts.append(font_path)
    return available_fonts


def get_measurement_font(font_path: Optional[str], font_size: int):
    """Return a PIL font used only for deterministic caption wrapping."""
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except OSError:
            pass
    return ImageFont.load_default()


def measure_text_width(text: str, font_path: Optional[str], font_size: int) -> int:
    """Measure text width without asking MoviePy to split words."""
    font = get_measurement_font(font_path, font_size)
    bbox = font.getbbox(text)
    return max(int(bbox[2] - bbox[0]), 0)


def wrap_caption_line(line: str, font_path: Optional[str], font_size: int, max_width: int) -> list[str]:
    """Wrap a single line at whitespace boundaries only."""
    words = re.findall(r"\S+", line)
    if not words:
        return [""]

    lines: list[str] = []
    current = words[0]

    for word in words[1:]:
        candidate = f"{current} {word}"
        if measure_text_width(candidate, font_path, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def wrap_caption_text(text: str, font_path: Optional[str], font_size: int, max_width: int) -> str:
    """Create manual line breaks so MoviePy does not hyphenate or split words."""
    normalized_text = unicodedata.normalize("NFC", text)
    wrapped_lines: list[str] = []

    for raw_line in normalized_text.splitlines() or [normalized_text]:
        wrapped_lines.extend(wrap_caption_line(raw_line.strip(), font_path, font_size, max_width))

    return "\n".join(line for line in wrapped_lines if line)


def load_timing_plan(paths: PipelinePaths) -> dict:
    """Load block timing metadata."""
    if not paths.timing_plan_file.exists():
        raise FileNotFoundError(f"Missing timing plan file: {paths.timing_plan_file}")
    return json.loads(paths.timing_plan_file.read_text(encoding="utf-8"))


def load_captions(paths: PipelinePaths) -> list[dict]:
    """Parse SRT captions into a list of timed subtitle entries."""
    if not paths.captions_file.exists():
        raise FileNotFoundError(f"Missing captions file: {paths.captions_file}")

    raw_text = paths.captions_file.read_text(encoding="utf-8-sig").strip()

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
                "text": unicodedata.normalize("NFC", "\n".join(lines[2:])),
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


def get_scene_video_path(paths: PipelinePaths, block_id: str) -> Path:
    """Return the expected stock video path for a block."""
    return paths.assets_dir / f"{block_id}.mp4"


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


def make_background_clip(paths: PipelinePaths, block_id: str, duration: float):
    """Create the background clip for one block."""
    video_path = get_scene_video_path(paths, block_id)

    if not video_path.exists():
        print(f"Block {block_id}: asset missing, using dark fallback background")
        return set_clip_duration(make_fallback_background(duration), duration)

    print(f"Block {block_id}: using asset {video_path.name}")

    background_clip = VideoFileClip(str(video_path))
    background_clip = remove_clip_audio(background_clip)
    background_clip = loop_or_trim_clip(background_clip, duration)
    background_clip = fit_clip_to_vertical_frame(background_clip)
    return set_clip_duration(background_clip, duration)


def build_text_clip(text: str, duration: float, font_size: int, language: str):
    """Create one timed text clip attempt for a specific font size."""
    font_candidates = get_available_caption_fonts(language) + [None]
    last_error = None

    for font_name in font_candidates:
        wrapped_text = wrap_caption_text(text, font_name, font_size, CAPTION_TEXT_WIDTH)
        try:
            return TextClip(
                text=wrapped_text,
                font=font_name,
                font_size=font_size,
                color=CAPTION_COLOR,
                stroke_color=CAPTION_STROKE_COLOR,
                stroke_width=CAPTION_STROKE_WIDTH,
                method="label",
                text_align="center",
                horizontal_align="center",
                vertical_align="center",
                interline=CAPTION_INTERLINE,
                duration=duration,
            )
        except Exception as error:
            last_error = error

        try:
            clip = TextClip(
                txt=wrapped_text,
                font=font_name,
                fontsize=font_size,
                color=CAPTION_COLOR,
                stroke_color=CAPTION_STROKE_COLOR,
                stroke_width=CAPTION_STROKE_WIDTH,
                method="label",
                align="center",
                interline=CAPTION_INTERLINE,
            )
            return set_clip_duration(clip, duration)
        except Exception as error:
            last_error = error

    raise RuntimeError(f"Could not create text clip: {last_error}")


def make_caption_clip(text: str, start_time: float, end_time: float, language: str):
    """Create one caption overlay clip from an SRT subtitle entry."""
    duration = max(end_time - start_time, 0.01)
    chosen_text_clip = None

    for font_size in FONT_SIZES:
        text_clip = build_text_clip(text, duration, font_size, language)

        if text_clip.w <= CAPTION_BOX_WIDTH and text_clip.h <= CAPTION_MAX_HEIGHT:
            chosen_text_clip = text_clip
            break

        if chosen_text_clip is None:
            chosen_text_clip = text_clip

    if chosen_text_clip is None:
        raise RuntimeError("Could not build a caption clip.")

    box_width = min(
        max(chosen_text_clip.w + (CAPTION_HORIZONTAL_PADDING * 2), CAPTION_BOX_WIDTH),
        VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2),
    )
    box_height = chosen_text_clip.h + (CAPTION_VERTICAL_PADDING * 2)
    box_clip = ColorClip(
        size=(int(box_width), int(box_height)),
        color=(0, 0, 0),
        duration=duration,
    )
    box_clip = set_clip_opacity(box_clip, 0.45)

    text_x = (box_width - chosen_text_clip.w) / 2
    text_y = max((box_height - chosen_text_clip.h) / 2, CAPTION_VERTICAL_PADDING / 2)
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


def make_scene_clip(paths: PipelinePaths, timing_block: dict):
    """Build one background block clip."""
    background_clip = make_background_clip(paths, timing_block["id"], timing_block["duration"])
    return set_clip_duration(background_clip, timing_block["duration"])


def build_background_video(paths: PipelinePaths, audio_duration: float, timing_blocks: list):
    """Build the full background track from authored block timings."""
    if not timing_blocks:
        raise ValueError("timing_plan.json does not contain any blocks.")

    print(f"Audio duration: {audio_duration:.2f} seconds")
    print(f"Number of blocks: {len(timing_blocks)}")

    for scene in timing_blocks:
        print(
            f"Block {scene['id']}: "
            f"start={scene['start']:.2f}s, "
            f"end={scene['end']:.2f}s, "
            f"duration={scene['duration']:.2f}s"
        )

    scene_clips = [make_scene_clip(paths, scene) for scene in timing_blocks]
    video = concatenate_videoclips(scene_clips, method="compose")
    return set_clip_duration(video, audio_duration)


def build_caption_overlays(captions: list[dict], language: str) -> list:
    """Build timed caption overlay clips from SRT entries."""
    overlays = []

    for caption in captions:
        overlays.append(
            make_caption_clip(
                text=caption["text"],
                start_time=caption["start"],
                end_time=caption["end"],
                language=language,
            )
        )

    return overlays


def render_video(paths: PipelinePaths, script: dict, timing_plan: dict, audio_clip, captions: list[dict]):
    """Render the final video using block timings and SRT caption overlays."""
    timing_blocks = timing_plan.get("blocks", [])
    background_video = build_background_video(paths, audio_clip.duration, timing_blocks)
    language = script.get("language", "")
    caption_overlays = build_caption_overlays(captions, language)
    available_fonts = get_available_caption_fonts(language)

    final_layers = [background_video] + caption_overlays
    final_video = CompositeVideoClip(final_layers, size=VIDEO_SIZE)
    final_video = set_clip_duration(final_video, audio_clip.duration)
    final_video = set_clip_audio(final_video, audio_clip)

    if available_fonts:
        print(f"Caption font: {available_fonts[0]}")
    else:
        print("Caption font: no known font file found, using MoviePy default")
    print(f"Render language: {script.get('language', 'unknown')}")
    print(f"Loaded {len(captions)} caption entries from captions.srt")
    print(f"Final video duration: {final_video.duration:.2f} seconds")
    return final_video


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Render a final video for one pipeline run.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Render the final MP4 from script scenes, narration, and timed SRT captions."""
    args = parse_args()
    paths = build_pipeline_paths(args.input, args.output)

    if not paths.audio_file.exists():
        raise FileNotFoundError(f"Missing narration file: {paths.audio_file}")

    script = load_script(paths)
    timing_plan = load_timing_plan(paths)
    captions = load_captions(paths)
    audio_clip = AudioFileClip(str(paths.audio_file))

    final_video = render_video(paths, script, timing_plan, audio_clip, captions)

    final_video.write_videofile(
        str(paths.final_video_file),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
    )

    final_video.close()
    audio_clip.close()

    print(f"Created {paths.final_video_file}")


if __name__ == "__main__":
    main()
