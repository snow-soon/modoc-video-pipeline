"""Build the production script from a human-authored script plan."""

import json
import re
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_FILE = BASE_DIR / "input" / "script_plan.example.json"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = OUTPUT_DIR / "script.json"
NARRATION_TEXT_FILE = OUTPUT_DIR / "narration.txt"

SAFE_BLOCK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def normalize_text(text: str) -> str:
    """Collapse whitespace while preserving natural sentence spacing."""
    return re.sub(r"\s+", " ", text).strip()


def normalize_text_list(
    value: object, field_name: str, block_id: Optional[str] = None
) -> list[str]:
    """Validate and normalize a non-empty list of strings."""
    if not isinstance(value, list):
        location = f"block '{block_id}'" if block_id else "script plan"
        raise ValueError(f"{location}: '{field_name}' must be a list.")

    normalized_items = [normalize_text(str(item)) for item in value if normalize_text(str(item))]
    if not normalized_items:
        location = f"block '{block_id}'" if block_id else "script plan"
        raise ValueError(f"{location}: '{field_name}' must be non-empty.")

    deduped_items = []
    seen = set()
    for item in normalized_items:
        if item not in seen:
            deduped_items.append(item)
            seen.add(item)
    return deduped_items


def load_script_plan() -> dict:
    """Read the human-authored script plan JSON file."""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input script plan: {INPUT_FILE}")

    try:
        return json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {INPUT_FILE}: {error}") from error


def validate_block_id(block_id: str, seen_ids: set[str]) -> str:
    """Validate one block id."""
    normalized_id = normalize_text(block_id)
    if not normalized_id:
        raise ValueError("Each block requires a non-empty 'id'.")
    if not SAFE_BLOCK_ID_PATTERN.match(normalized_id):
        raise ValueError(
            f"Block id '{normalized_id}' is invalid. Use only letters, numbers, '_' or '-'."
        )
    if normalized_id in seen_ids:
        raise ValueError(f"Duplicate block id found: '{normalized_id}'.")
    seen_ids.add(normalized_id)
    return normalized_id


def validate_script_plan(script_plan: dict) -> dict:
    """Validate and normalize the human-authored plan."""
    if not isinstance(script_plan, dict):
        raise ValueError("Script plan must be a JSON object.")

    title = normalize_text(str(script_plan.get("title", "")))
    if not title:
        raise ValueError("Script plan requires a non-empty 'title'.")

    language = normalize_text(str(script_plan.get("language", "")))
    if not language:
        raise ValueError("Script plan requires a non-empty 'language'.")

    blocks = script_plan.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("Script plan requires a non-empty 'blocks' list.")

    global_avoid_visuals = script_plan.get("global_avoid_visuals", [])
    if global_avoid_visuals:
        global_avoid_visuals = normalize_text_list(global_avoid_visuals, "global_avoid_visuals")
    else:
        global_avoid_visuals = []

    seen_ids: set[str] = set()
    normalized_blocks = []

    for index, raw_block in enumerate(blocks, start=1):
        if not isinstance(raw_block, dict):
            raise ValueError(f"Block {index} must be a JSON object.")

        block_id = validate_block_id(str(raw_block.get("id", "")), seen_ids)
        narration = normalize_text(str(raw_block.get("narration", "")))
        if not narration:
            raise ValueError(f"block '{block_id}': 'narration' must be non-empty.")

        captions = normalize_text_list(raw_block.get("captions"), "captions", block_id)
        visual_keywords = normalize_text_list(
            raw_block.get("visual_keywords"), "visual_keywords", block_id
        )
        avoid_visuals = raw_block.get("avoid_visuals", [])
        if avoid_visuals:
            avoid_visuals = normalize_text_list(avoid_visuals, "avoid_visuals", block_id)
        else:
            avoid_visuals = []

        merged_avoid_visuals = []
        seen_visuals = set()
        for item in global_avoid_visuals + avoid_visuals:
            if item not in seen_visuals:
                merged_avoid_visuals.append(item)
                seen_visuals.add(item)

        normalized_blocks.append(
            {
                "id": block_id,
                "narration": narration,
                "captions": captions,
                "visual_keywords": visual_keywords,
                "avoid_visuals": merged_avoid_visuals,
            }
        )

    full_narration = "\n".join(block["narration"] for block in normalized_blocks)
    if not normalize_text(full_narration):
        raise ValueError("Combined narration is empty after normalization.")

    return {
        "title": title,
        "language": language,
        "narration": full_narration,
        "blocks": normalized_blocks,
    }


def write_outputs(script: dict) -> None:
    """Write script.json and narration.txt."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    SCRIPT_FILE.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    NARRATION_TEXT_FILE.write_text(script["narration"], encoding="utf-8")


def generate_script() -> dict:
    """Parse the human-authored script plan and build pipeline outputs."""
    script_plan = load_script_plan()
    script = validate_script_plan(script_plan)
    write_outputs(script)

    print(f"Blocks: {len(script['blocks'])}")
    print(f"Narration characters: {len(script['narration'])}")
    print(f"Wrote {SCRIPT_FILE}")
    print(f"Wrote {NARRATION_TEXT_FILE}")
    return script


def main() -> None:
    """Build output files from the human-authored script plan."""
    generate_script()


if __name__ == "__main__":
    main()
