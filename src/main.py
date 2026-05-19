"""Generate MVP output files for the sample short-form video pipeline."""

import json
from pathlib import Path


# Resolve project paths from this file so the script works from any directory.
BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_FILE = BASE_DIR / "input" / "medical_qna.example.txt"
OUTPUT_DIR = BASE_DIR / "output"


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert seconds into the SRT timestamp format HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))

    if milliseconds == 1000:
        whole_seconds += 1
        milliseconds = 0

    if whole_seconds == 60:
        minutes += 1
        whole_seconds = 0

    if minutes == 60:
        hours += 1
        minutes = 0

    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def build_script(source_text: str) -> dict:
    """Return a hardcoded example script while preserving the source input."""
    return {
        "title": "Toddler Neck Swelling: What Parents Should Watch",
        "source_file": str(INPUT_FILE.relative_to(BASE_DIR)),
        "source_text": source_text.strip(),
        "narration": (
            "A toddler comes home with sudden swelling on the back of the neck. "
            "That can be scary for any parent.\n\n"
            "A delayed reaction from an old mosquito bite is one possible cause. "
            "A mild skin irritation or an early local infection could also explain it.\n\n"
            "If the child seems comfortable and has no fever, the situation may not be urgent. "
            "But parents should watch for spreading redness, pain, discharge, or worsening swelling.\n\n"
            "If those warning signs appear, or the swelling does not improve, a pediatric or dermatology visit is a good next step."
        ),
        "scenes": [
            {
                "start": 0.0,
                "end": 4.0,
                "caption": "Sudden neck swelling can worry any parent",
                "visual_keyword": "parent checking toddler neck",
            },
            {
                "start": 4.0,
                "end": 8.0,
                "caption": "An old mosquito bite can flare up again",
                "visual_keyword": "mosquito bite child skin",
            },
            {
                "start": 8.0,
                "end": 12.0,
                "caption": "Skin irritation or mild infection is also possible",
                "visual_keyword": "child skin irritation close up",
            },
            {
                "start": 12.0,
                "end": 16.0,
                "caption": "No fever or pain may mean it is less urgent",
                "visual_keyword": "calm toddler home care",
            },
            {
                "start": 16.0,
                "end": 21.0,
                "caption": "Watch for redness, discharge, or worsening swelling",
                "visual_keyword": "parent monitoring symptoms",
            },
            {
                "start": 21.0,
                "end": 26.0,
                "caption": "See a doctor if warning signs appear",
                "visual_keyword": "pediatric consultation",
            },
        ],
    }


def write_script_json(script: dict) -> None:
    """Save the structured video script as JSON."""
    output_path = OUTPUT_DIR / "script.json"
    output_path.write_text(
        json.dumps(script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_narration_text(script: dict) -> None:
    """Save the narration in plain text for future TTS work."""
    output_path = OUTPUT_DIR / "narration.txt"
    output_path.write_text(script["narration"], encoding="utf-8")


def write_captions_srt(script: dict) -> None:
    """Create one caption block per scene in standard SRT format."""
    lines = []

    for index, scene in enumerate(script["scenes"], start=1):
        lines.append(str(index))
        lines.append(
            f"{seconds_to_srt_timestamp(scene['start'])} --> "
            f"{seconds_to_srt_timestamp(scene['end'])}"
        )
        lines.append(scene["caption"])
        lines.append("")

    output_path = OUTPUT_DIR / "captions.srt"
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> None:
    """Read the sample input and generate all MVP output files."""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    # Ensure the output directory exists before writing files.
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Read the sample medical Q&A text. The MVP keeps parsing logic simple for now.
    source_text = INPUT_FILE.read_text(encoding="utf-8")

    # Build a hardcoded example script that future LLM steps can replace.
    script = build_script(source_text)

    # Write the three MVP artifacts used by the rest of the pipeline.
    write_script_json(script)
    write_narration_text(script)
    write_captions_srt(script)

    print("Created output/script.json")
    print("Created output/narration.txt")
    print("Created output/captions.srt")


if __name__ == "__main__":
    main()
