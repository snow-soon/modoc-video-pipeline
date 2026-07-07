"""Generate captions and block timings from authored captions and narration."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import wave
from typing import Dict, List, Tuple

from pipeline_paths import PipelinePaths, build_pipeline_paths


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert seconds into SRT timestamp format."""
    total_milliseconds = int(round(seconds * 1000))

    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    secs = (total_milliseconds % 60_000) // 1000
    milliseconds = total_milliseconds % 1000

    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def load_script(paths: PipelinePaths) -> Dict:
    """Read the normalized script JSON."""
    if not paths.script_file.exists():
        raise FileNotFoundError(f"Missing script file: {paths.script_file}")
    return json.loads(paths.script_file.read_text(encoding="utf-8"))


def get_audio_duration(paths: PipelinePaths) -> float:
    """Read the WAV duration without requiring MoviePy."""
    if not paths.audio_file.exists():
        raise FileNotFoundError(f"Missing narration audio file: {paths.audio_file}")

    with wave.open(str(paths.audio_file), "rb") as wave_file:
        frame_count = wave_file.getnframes()
        frame_rate = wave_file.getframerate()

    return frame_count / float(frame_rate)


def text_weight(text: str) -> int:
    """Calculate timing weight from non-space text length."""
    return max(len(re.sub(r"\s+", "", text or "")), 1)


def distribute_segments(texts: List[str], start_time: float, end_time: float) -> List[Tuple[float, float]]:
    """Assign timings proportionally to text weights inside a time span."""
    weights = [text_weight(text) for text in texts]
    total_weight = sum(weights)
    duration = max(end_time - start_time, 0.0)
    current_start = start_time
    segments = []

    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            current_end = end_time
        else:
            current_end = current_start + (duration * weight / total_weight)

        segments.append((current_start, current_end))
        current_start = current_end

    return segments


def write_captions_srt(paths: PipelinePaths, captions: List[Dict]) -> None:
    """Save the authored captions in SRT format."""
    lines = []

    for caption in captions:
        lines.append(str(caption["index"]))
        lines.append(
            f"{seconds_to_srt_timestamp(caption['start'])} --> "
            f"{seconds_to_srt_timestamp(caption['end'])}"
        )
        lines.append(unicodedata.normalize("NFC", caption["text"]))
        lines.append("")

    paths.captions_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8-sig")
    print(f"Created {paths.captions_file}")


def build_timing_plan(script: Dict, audio_duration: float) -> Tuple[List[Dict], Dict]:
    """Build caption timings and per-block timing metadata."""
    blocks = script.get("blocks", [])
    if not blocks:
        raise ValueError("script.json does not contain any blocks.")

    block_narrations = [block["narration"] for block in blocks]
    block_ranges = distribute_segments(block_narrations, 0.0, audio_duration)

    caption_entries = []
    timing_blocks = []
    caption_index = 1

    for block, (block_start, block_end) in zip(blocks, block_ranges):
        block_captions = block.get("captions") or [block["narration"]]
        caption_ranges = distribute_segments(block_captions, block_start, block_end)
        timing_captions = []

        for caption_text, (caption_start, caption_end) in zip(block_captions, caption_ranges):
            caption_entry = {
                "index": caption_index,
                "start": caption_start,
                "end": caption_end,
                "text": caption_text,
                "block_id": block["id"],
            }
            caption_entries.append(caption_entry)
            timing_captions.append(
                {
                    "index": caption_index,
                    "start": caption_start,
                    "end": caption_end,
                    "text": caption_text,
                }
            )
            caption_index += 1

        timing_blocks.append(
            {
                "id": block["id"],
                "start": block_start,
                "end": block_end,
                "duration": block_end - block_start,
                "narration": block["narration"],
                "captions": timing_captions,
                "visual_keywords": block.get("visual_keywords", []),
                "avoid_visuals": block.get("avoid_visuals", []),
            }
        )

    timing_plan = {
        "title": script.get("title", ""),
        "language": script.get("language", ""),
        "audio_duration": audio_duration,
        "blocks": timing_blocks,
    }
    return caption_entries, timing_plan


def generate_captions(paths: PipelinePaths) -> str:
    """Generate SRT captions and timing plan from authored captions."""
    script = load_script(paths)
    audio_duration = get_audio_duration(paths)
    caption_timings, timing_plan = build_timing_plan(script, audio_duration)

    print(f"Narration duration: {audio_duration:.2f} seconds")
    print(f"Generated {len(caption_timings)} authored caption entries")

    write_captions_srt(paths, caption_timings)
    paths.timing_plan_file.write_text(
        json.dumps(timing_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Created {paths.timing_plan_file}")
    return str(paths.captions_file)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Generate captions and timing plan for one run.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Generate SRT captions and timing plan."""
    args = parse_args()
    generate_captions(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
