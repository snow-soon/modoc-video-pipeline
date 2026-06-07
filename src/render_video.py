"""Render a vertical video using block-based timing and authored captions."""

import json
from pathlib import Path

try:
    from moviepy import (
        AudioFileClip,
        ColorClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )
except ImportError:
    from moviepy.editor import (  # type: ignore
        AudioFileClip,
        ColorClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )


BASE_DIR = Path(__file__).resolve().parents[1]
TIMING_PLAN_FILE = BASE_DIR / "output" / "timing_plan.json"
AUDIO_FILE = BASE_DIR / "output" / "narration.wav"
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
CAPTION_BOX_WIDTH = 920
CAPTION_MAX_HEIGHT = 360
CAPTION_TOP_RATIO = 0.68
CAPTION_SIDE_MARGIN = 80
CAPTION_BOTTOM_MARGIN = 160
FONT_SIZES = [76, 72, 68, 64, 60, 56, 52]
KOREAN_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def set_clip_position(clip, position):
    if hasattr(clip, "with_position"):
        return clip.with_position(position)
    return clip.set_position(position)


def set_clip_duration(clip, duration: float):
    if hasattr(clip, "with_duration"):
        return clip.with_duration(duration)
    return clip.set_duration(duration)


def set_clip_audio(clip, audio_clip):
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio_clip)
    return clip.set_audio(audio_clip)


def set_clip_start(clip, start_time: float):
    if hasattr(clip, "with_start"):
        return clip.with_start(start_time)
    return clip.set_start(start_time)


def set_clip_opacity(clip, opacity: float):
    if hasattr(clip, "with_opacity"):
        return clip.with_opacity(opacity)
    return clip.set_opacity(opacity)


def remove_clip_audio(clip):
    if hasattr(clip, "without_audio"):
        return clip.without_audio()
    return clip.set_audio(None)


def resize_clip(clip, width=None, height=None):
    if hasattr(clip, "resized"):
        return clip.resized(width=width, height=height)
    return clip.resize(width=width, height=height)


def crop_clip(clip, x1: float, y1: float, x2: float, y2: float):
    if hasattr(clip, "cropped"):
        return clip.cropped(x1=x1, y1=y1, x2=x2, y2=y2)
    return clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)


def trim_clip(clip, start_time: float, end_time: float):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start_time, end_time)
    return clip.subclip(start_time, end_time)


def load_timing_plan() -> dict:
    """Read timing_plan.json."""
    if not TIMING_PLAN_FILE.exists():
        raise FileNotFoundError(f"Missing timing plan: {TIMING_PLAN_FILE}")
    return json.loads(TIMING_PLAN_FILE.read_text(encoding="utf-8"))


def get_available_korean_fonts() -> list[str]:
    """Return existing Korean font file paths."""
    return [font_path for font_path in KOREAN_FONT_CANDIDATES if Path(font_path).exists()]


def make_fallback_background(duration: float):
    """Create a simple dark fallback background."""
    return ColorClip(size=VIDEO_SIZE, color=BACKGROUND_COLOR, duration=duration)


def loop_or_trim_clip(clip, duration: float):
    """Fit one background clip to the exact block duration."""
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
    """Resize and crop one clip to 1080x1920."""
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


def get_block_video_path(block_id: str) -> Path:
    """Return the expected asset path for one block."""
    return ASSETS_DIR / f"{block_id}.mp4"


def make_background_clip(block_id: str, duration: float):
    """Create one block background clip."""
    video_path = get_block_video_path(block_id)
    if not video_path.exists():
        print(f"Block {block_id}: asset missing, using dark fallback background")
        return set_clip_duration(make_fallback_background(duration), duration)

    print(f"Block {block_id}: using asset {video_path.name}")
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
    """Create one caption overlay clip from timing_plan.json."""
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

    box_width = min(chosen_text_clip.w + 56, VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2))
    box_height = min(chosen_text_clip.h + 44, CAPTION_MAX_HEIGHT + 44)
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


def build_background_video(blocks: list[dict], audio_duration: float):
    """Build the full background track from authored block timings."""
    scene_clips = []
    for block in blocks:
        print(
            f"Block {block['id']}: start={block['start']:.2f}s, "
            f"end={block['end']:.2f}s, duration={block['duration']:.2f}s"
        )
        scene_clips.append(make_background_clip(block["id"], block["duration"]))

    video = concatenate_videoclips(scene_clips, method="compose")
    return set_clip_duration(video, audio_duration)


def build_caption_overlays(blocks: list[dict]) -> list:
    """Build timed caption overlays from timing_plan.json."""
    overlays = []
    for block in blocks:
        for caption in block["captions"]:
            overlays.append(
                make_caption_clip(
                    text=caption["text"],
                    start_time=caption["start"],
                    end_time=caption["end"],
                )
            )
    return overlays


def render_video():
    """Render the final MP4 from timing_plan.json, narration.wav, and block assets."""
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Missing narration file: {AUDIO_FILE}")

    timing_plan = load_timing_plan()
    blocks = timing_plan.get("blocks", [])
    if not blocks:
        raise ValueError("timing_plan.json does not contain any blocks.")

    audio_clip = AudioFileClip(str(AUDIO_FILE))
    background_video = build_background_video(blocks, audio_clip.duration)
    caption_overlays = build_caption_overlays(blocks)
    final_layers = [background_video] + caption_overlays

    final_video = CompositeVideoClip(final_layers, size=VIDEO_SIZE)
    final_video = set_clip_duration(final_video, audio_clip.duration)
    final_video = set_clip_audio(final_video, audio_clip)

    print(f"Audio duration: {audio_clip.duration:.2f} seconds")
    print(f"Number of blocks: {len(blocks)}")

    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
    )

    final_video.close()
    audio_clip.close()
    print(f"Final output path: {OUTPUT_FILE}")
    return OUTPUT_FILE


def main() -> None:
    """Render the final block-timed vertical video."""
    render_video()


if __name__ == "__main__":
    main()
