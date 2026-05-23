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
        "title": "Fever Medicine Dosage for a 14kg Child",
        "source_file": str(INPUT_FILE.relative_to(BASE_DIR)),
        "source_text": source_text.strip(),
        "narration": (
            "Most parents don't know this.\n\n"
            "If your child is around 14 kilograms, giving the wrong fever medicine dose can be dangerous.\n\n"
            "For acetaminophen, the typical amount is about 4.5 to 6.7 milliliters, depending on the medicine concentration.\n\n"
            "For ibuprofen, it is about 3.6 to 7.2 milliliters.\n\n"
            "But the most important step is to check the concentration on the medicine label.\n\n"
            "Never use a regular spoon. Always use the measuring cup or syringe that comes with the medicine.\n\n"
            "If you are not sure, ask a doctor or pharmacist."
        ),
        "scenes": [
            {
                "start": 0.0,
                "end": 3.0,
                "caption": "Most parents don't know this",
                "visual_keyword": "worried parent sick child",
            },
            {
                "start": 3.0,
                "end": 7.0,
                "caption": "Wrong fever dose can be dangerous",
                "visual_keyword": "child fever thermometer",
            },
            {
                "start": 7.0,
                "end": 11.0,
                "caption": "Around 14kg child",
                "visual_keyword": "toddler weight scale",
            },
            {
                "start": 11.0,
                "end": 16.0,
                "caption": "Acetaminophen: about 4.5-6.7 mL",
                "visual_keyword": "children acetaminophen syrup",
            },
            {
                "start": 16.0,
                "end": 21.0,
                "caption": "Ibuprofen: about 3.6-7.2 mL",
                "visual_keyword": "children ibuprofen medicine",
            },
            {
                "start": 21.0,
                "end": 26.0,
                "caption": "Always check the medicine label",
                "visual_keyword": "medicine label close up",
            },
            {
                "start": 26.0,
                "end": 30.0,
                "caption": "Use a measuring cup, not a spoon",
                "visual_keyword": "medicine measuring cup syringe",
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
