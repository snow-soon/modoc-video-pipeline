"""Generate captions and timing plans from human-authored caption blocks."""

import json
import wave
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = OUTPUT_DIR / "script.json"
AUDIO_FILE = OUTPUT_DIR / "narration.wav"
CAPTIONS_FILE = OUTPUT_DIR / "captions.srt"
TIMING_PLAN_FILE = OUTPUT_DIR / "timing_plan.json"


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert seconds into SRT timestamp format."""
    total_milliseconds = int(round(seconds * 1000))
    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    secs = (total_milliseconds % 60_000) // 1000
    milliseconds = total_milliseconds % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def load_script() -> dict:
    """Read the parsed script JSON."""
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Missing script file: {SCRIPT_FILE}")
    return json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))


def get_audio_duration() -> float:
    """Read the WAV duration."""
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Missing narration audio file: {AUDIO_FILE}")

    with wave.open(str(AUDIO_FILE), "rb") as wave_file:
        frame_count = wave_file.getnframes()
        frame_rate = wave_file.getframerate()

    return frame_count / float(frame_rate)


def build_timing_plan(script: dict, audio_duration: float) -> dict:
    """Assign block and caption timings proportionally to authored text length."""
    blocks = script.get("blocks", [])
    if not blocks:
        raise ValueError("script.json does not contain any blocks.")

    narration_weights = [max(len(block["narration"].replace(" ", "")), 1) for block in blocks]
    total_weight = sum(narration_weights)

    timed_blocks = []
    current_start = 0.0

    for index, (block, block_weight) in enumerate(zip(blocks, narration_weights), start=1):
        if index == len(blocks):
            block_end = audio_duration
        else:
            block_end = current_start + (audio_duration * block_weight / total_weight)

        block_duration = max(block_end - current_start, 0.0)
        caption_weights = [max(len(caption.replace(" ", "")), 1) for caption in block["captions"]]
        caption_total_weight = sum(caption_weights)

        timed_captions = []
        caption_start = current_start
        for caption_index, (caption_text, caption_weight) in enumerate(
            zip(block["captions"], caption_weights), start=1
        ):
            if caption_index == len(block["captions"]):
                caption_end = block_end
            else:
                caption_end = caption_start + (block_duration * caption_weight / caption_total_weight)

            timed_captions.append(
                {
                    "text": caption_text,
                    "start": caption_start,
                    "end": caption_end,
                }
            )
            caption_start = caption_end

        timed_blocks.append(
            {
                "id": block["id"],
                "start": current_start,
                "end": block_end,
                "duration": block_duration,
                "captions": timed_captions,
            }
        )
        current_start = block_end

    if timed_blocks:
        timed_blocks[-1]["end"] = audio_duration
        timed_blocks[-1]["duration"] = audio_duration - timed_blocks[-1]["start"]
        if timed_blocks[-1]["captions"]:
            timed_blocks[-1]["captions"][-1]["end"] = audio_duration

    return {
        "audio_duration": audio_duration,
        "blocks": timed_blocks,
    }


def write_timing_plan(timing_plan: dict) -> None:
    """Save timing_plan.json."""
    TIMING_PLAN_FILE.write_text(
        json.dumps(timing_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Created {TIMING_PLAN_FILE}")


def write_captions_srt(timing_plan: dict) -> None:
    """Save captions.srt from the timed caption blocks."""
    lines = []
    caption_index = 1

    for block in timing_plan["blocks"]:
        for caption in block["captions"]:
            lines.append(str(caption_index))
            lines.append(
                f"{seconds_to_srt_timestamp(caption['start'])} --> "
                f"{seconds_to_srt_timestamp(caption['end'])}"
            )
            lines.append(caption["text"])
            lines.append("")
            caption_index += 1

    CAPTIONS_FILE.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"Created {CAPTIONS_FILE}")


def generate_captions() -> Path:
    """Generate timing_plan.json and captions.srt from human-authored caption blocks."""
    script = load_script()
    audio_duration = get_audio_duration()
    timing_plan = build_timing_plan(script, audio_duration)
    write_timing_plan(timing_plan)
    write_captions_srt(timing_plan)

    total_captions = sum(len(block["captions"]) for block in timing_plan["blocks"])
    final_subtitle_end = 0.0
    if timing_plan["blocks"] and timing_plan["blocks"][-1]["captions"]:
        final_subtitle_end = timing_plan["blocks"][-1]["captions"][-1]["end"]

    print(f"Audio duration: {audio_duration:.2f} seconds")
    print(f"Number of blocks: {len(timing_plan['blocks'])}")
    print(f"Number of captions: {total_captions}")
    print(f"Final subtitle end time: {final_subtitle_end:.2f} seconds")
    return CAPTIONS_FILE


def main() -> None:
    """Generate block-based captions and timing data."""
    generate_captions()


if __name__ == "__main__":
    main()
