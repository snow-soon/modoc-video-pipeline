"""Normalize a human-authored script plan into pipeline runtime files."""

from __future__ import annotations

import argparse
import json
import unicodedata
from typing import Any, Dict, List

from pipeline_paths import PipelinePaths, build_pipeline_paths


def normalize_text(value: str, context: str) -> str:
    """Normalize authored text without changing wording."""
    normalized = unicodedata.normalize("NFC", value.strip())
    if "\ufffd" in normalized:
        raise ValueError(f"{context} contains the Unicode replacement character; check file encoding.")
    return normalized


def require_non_empty_string(data: Dict[str, Any], key: str, context: str) -> str:
    """Return a required non-empty string field."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must include a non-empty '{key}' field.")
    return normalize_text(value, f"{context}.{key}")


def normalize_string_list(
    value: Any,
    field_name: str,
    context: str,
    allow_empty: bool = False,
) -> List[str]:
    """Normalize one-or-many string fields into a clean list."""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"{context} field '{field_name}' must be a string or list of strings.")

    normalized = []
    seen = set()

    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{context} field '{field_name}' contains an empty or invalid string.")

        cleaned = normalize_text(item, f"{context}.{field_name}")
        lowered = cleaned.lower()
        if lowered not in seen:
            seen.add(lowered)
            normalized.append(cleaned)

    if not normalized and not allow_empty:
        raise ValueError(f"{context} field '{field_name}' must not be empty.")

    return normalized


def build_narration(blocks: List[Dict[str, Any]], authored_narration: Any) -> str:
    """Use authored narration when present, otherwise join block narration."""
    if isinstance(authored_narration, str) and authored_narration.strip():
        return authored_narration.strip()

    return " ".join(block["narration"] for block in blocks).strip()


def normalize_block(
    block: Dict[str, Any],
    block_index: int,
    global_avoid_visuals: List[str],
) -> Dict[str, Any]:
    """Normalize one authored block into the runtime shape."""
    context = f"blocks[{block_index}]"
    block_id = block.get("id")

    if not isinstance(block_id, str) or not block_id.strip():
        block_id = f"block_{block_index + 1}"
    else:
        block_id = block_id.strip()

    narration = require_non_empty_string(block, "narration", context)
    captions = normalize_string_list(block.get("captions", []), "captions", context)
    visual_keywords = normalize_string_list(
        block.get("visual_keywords", []),
        "visual_keywords",
        context,
    )

    block_avoid_visuals = []
    if "avoid_visuals" in block:
        block_avoid_visuals = normalize_string_list(
            block.get("avoid_visuals", []),
            "avoid_visuals",
            context,
            allow_empty=True,
        )

    avoid_visuals = []
    seen = set()
    for item in [*global_avoid_visuals, *block_avoid_visuals]:
        lowered = item.lower()
        if lowered not in seen:
            seen.add(lowered)
            avoid_visuals.append(item)

    return {
        "id": block_id,
        "narration": narration,
        "captions": captions,
        "visual_keywords": visual_keywords,
        "avoid_visuals": avoid_visuals,
    }


def normalize_script_plan(script_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the authored script plan."""
    title = require_non_empty_string(script_plan, "title", "script_plan")
    language = require_non_empty_string(script_plan, "language", "script_plan")

    raw_blocks = script_plan.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError("script_plan must include a non-empty 'blocks' list.")

    global_avoid_visuals = []
    if "avoid_visuals" in script_plan:
        global_avoid_visuals = normalize_string_list(
            script_plan.get("avoid_visuals", []),
            "avoid_visuals",
            "script_plan",
            allow_empty=True,
        )

    blocks = []
    for index, raw_block in enumerate(raw_blocks):
        if not isinstance(raw_block, dict):
            raise ValueError(f"blocks[{index}] must be an object.")
        blocks.append(normalize_block(raw_block, index, global_avoid_visuals))

    narration = build_narration(blocks, script_plan.get("narration"))

    return {
        "title": title,
        "language": language,
        "narration": narration,
        "blocks": blocks,
    }


def load_script_plan(paths: PipelinePaths) -> Dict[str, Any]:
    """Load the authored script plan from disk."""
    if not paths.input_file.exists():
        raise FileNotFoundError(f"Missing input file: {paths.input_file}")

    with paths.input_file.open("r", encoding="utf-8") as file:
        return json.load(file)


def generate_script(paths: PipelinePaths) -> Dict[str, Any]:
    """Normalize the authored script plan and save runtime files."""
    paths.ensure_directories()

    script_plan = load_script_plan(paths)
    script = normalize_script_plan(script_plan)

    paths.script_file.write_text(
        json.dumps(script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.narration_text_file.write_text(script["narration"], encoding="utf-8")

    print(f"Created {paths.script_file}")
    print(f"Created {paths.narration_text_file}")
    print(f"Language: {script['language']}")
    print(f"Normalized {len(script['blocks'])} blocks from {paths.input_file}")
    return script


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Normalize a script plan for one video run.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Normalize and save the authored script JSON."""
    args = parse_args()
    generate_script(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
