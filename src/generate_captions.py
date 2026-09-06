"""Generate captions and block timings from authored captions and narration."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
import wave
from typing import Dict, List, Tuple

from pipeline_paths import PipelinePaths, build_pipeline_paths
from pipeline_state import atomic_json, file_digest, fingerprint, read_json
from generate_tts import speech_segments


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


def measured_ranges(script: Dict, manifest: Dict, audio_duration: float) -> Dict:
    """Validate sample-level evidence before using it for scene or caption timing."""
    if manifest.get("narration_fingerprint") != fingerprint(speech_segments(script)):
        raise ValueError("Speech segment manifest is stale for this script.")
    rate = manifest.get("sample_rate", 0)
    segments = manifest.get("segments", [])
    expected = speech_segments(script)
    if rate != 24000 or len(segments) != len(expected):
        raise ValueError("Invalid speech segment manifest.")
    offset = 0
    result = {}
    for segment, wanted in zip(segments, expected):
        start, end = segment.get("start_frame"), segment.get("end_frame")
        if any(segment.get(key) != value for key, value in wanted.items()):
            raise ValueError("Speech segment text or order changed.")
        if type(start) is not int or type(end) is not int or start != offset or end <= start:
            raise ValueError("Speech segment boundaries are invalid or discontinuous.")
        result.setdefault(segment["block_id"], []).append((start / rate, end / rate, segment["caption_index"]))
        offset = end
    if offset != manifest.get("total_frames") or abs(offset / rate - audio_duration) > 1 / rate:
        raise ValueError("Measured speech duration does not match narration.wav.")
    return result


def validate_timing_plan(script: Dict, plan: Dict, captions: List[Dict], audio_duration: float) -> None:
    """Fail closed on missing, reordered, overlapping, or stale timing data."""
    if not math.isfinite(audio_duration) or audio_duration <= 0:
        raise ValueError("Audio duration must be finite and positive.")
    blocks = plan.get("blocks", [])
    if not blocks or [b["id"] for b in blocks] != [b["id"] for b in script["blocks"]]:
        raise ValueError("Timing blocks do not match the script.")
    if abs(plan.get("audio_duration", 0) - audio_duration) > .002:
        raise ValueError("Timing plan has a stale audio duration.")
    flattened = []
    for source, block in zip(script["blocks"], blocks):
        if block.get("narration") != source["narration"]:
            raise ValueError("Timing plan has stale narration.")
        entries = block.get("captions", [])
        if [c["text"] for c in entries] != source["captions"]:
            raise ValueError("Timing captions do not match the script.")
        validate_ranges(entries, block["start"], block["end"])
        flattened.extend(entries)
    validate_ranges(blocks, 0, audio_duration)
    validate_ranges(captions, 0, audio_duration)
    if len(flattened) != len(captions):
        raise ValueError("Caption count differs from timing plan.")
    for expected, actual in zip(flattened, captions):
        if expected["text"] != actual["text"] or any(abs(expected[k] - actual[k]) > .002 for k in ("start", "end")):
            raise ValueError("SRT captions differ from timing plan.")


def validate_ranges(entries: List[Dict], start: float, end: float) -> None:
    cursor = start
    if not entries:
        raise ValueError("Empty caption or scene timeline.")
    for entry in entries:
        left, right = entry.get("start"), entry.get("end")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (left, right)):
            raise ValueError("Timeline contains non-finite boundaries.")
        if abs(left - cursor) > .002 or right - left < .001:
            raise ValueError("Timeline has gaps, overlaps, or zero-length entries.")
        cursor = right
    if abs(cursor - end) > .002:
        raise ValueError("Timeline does not cover its full duration.")


def build_timing_plan(script: Dict, audio_duration: float, manifest: Dict = None) -> Tuple[List[Dict], Dict]:
    """Build caption timings and per-block timing metadata."""
    blocks = script.get("blocks", [])
    if not blocks:
        raise ValueError("script.json does not contain any blocks.")

    measured = measured_ranges(script, manifest, audio_duration) if manifest else {}
    block_ranges = ([(measured[b["id"]][0][0], measured[b["id"]][-1][1]) for b in blocks]
                    if measured else distribute_segments([b["narration"] for b in blocks], 0.0, audio_duration))

    caption_entries = []
    timing_blocks = []
    caption_index = 1

    for block, (block_start, block_end) in zip(blocks, block_ranges):
        block_captions = block.get("captions") or [block["narration"]]
        caption_ranges = distribute_segments(block_captions, block_start, block_end)
        caption_basis = "estimated_text_weight"
        segment_ranges = measured.get(block["id"], [])
        if segment_ranges and all(segment[2] is not None for segment in segment_ranges):
            caption_ranges = [(start, end) for start, end, _ in segment_ranges]
            caption_basis = "measured_speech_segments"
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
                "caption_timing_basis": caption_basis,
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
        "scene_timing_basis": "measured_speech_segments" if measured else "estimated_text_weight",
        "blocks": timing_blocks,
    }
    validate_timing_plan(script, timing_plan, caption_entries, audio_duration)
    return caption_entries, timing_plan


def generate_captions(paths: PipelinePaths) -> str:
    """Generate SRT captions and timing plan from authored captions."""
    script = load_script(paths)
    audio_duration = get_audio_duration(paths)
    manifest = read_json(paths.audio_segments_file)
    if not manifest or manifest.get("audio_sha256") != file_digest(paths.audio_file):
        raise ValueError("Missing or stale measured audio manifest. Regenerate TTS before captions.")
    caption_timings, timing_plan = build_timing_plan(script, audio_duration, manifest)
    timing_plan["audio_sha256"] = manifest["audio_sha256"]

    print(f"Narration duration: {audio_duration:.2f} seconds")
    print(f"Generated {len(caption_timings)} authored caption entries")

    write_captions_srt(paths, caption_timings)
    atomic_json(paths.timing_plan_file, timing_plan)
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
