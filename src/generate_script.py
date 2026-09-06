"""Normalize a human-authored script plan into pipeline runtime files."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from typing import Any, Dict, List

from pipeline_paths import PipelinePaths, build_pipeline_paths


def normalize_text(value: str, context: str) -> str:
    """Normalize authored text without changing wording."""
    normalized = unicodedata.normalize("NFC", value.strip())
    if "\ufffd" in normalized:
        raise ValueError(f"{context} contains the Unicode replacement character; check file encoding.")
    if any(unicodedata.category(character) == "Cc" and character not in "\n\r\t" for character in normalized):
        raise ValueError(f"{context} contains an unexpected control character; check file encoding.")

    mojibake_pattern = re.compile(r"(?:Ã.|Â.|â(?:€|€™|€œ|€\x9d|€“|€”))")
    if mojibake_pattern.search(normalized):
        raise ValueError(f"{context} appears to contain mojibake; save the source as UTF-8.")
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
    deduplicate: bool = True,
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
        if not deduplicate or lowered not in seen:
            seen.add(lowered)
            normalized.append(cleaned)

    if not normalized and not allow_empty:
        raise ValueError(f"{context} field '{field_name}' must not be empty.")

    return normalized


def build_narration(blocks: List[Dict[str, Any]], authored_narration: Any) -> str:
    """Use authored narration when present, otherwise join block narration."""
    if isinstance(authored_narration, str) and authored_narration.strip():
        narration = normalize_text(authored_narration, "script_plan.narration")
        joined = " ".join(block["narration"] for block in blocks)
        if narration.split() != joined.split():
            raise ValueError("Full narration differs from block narration; audio and scenes would disagree.")
        return narration

    return " ".join(block["narration"] for block in blocks).strip()


def normalize_medical_sources(value: Any) -> List[Dict[str, str]]:
    """Normalize optional authoritative source notes used by Gemini reviewers."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("script_plan.medical_sources must be a list.")

    normalized_sources = []
    for index, source in enumerate(value):
        context = f"script_plan.medical_sources[{index}]"
        if not isinstance(source, dict):
            raise ValueError(f"{context} must be an object.")

        title = require_non_empty_string(source, "title", context)
        url = require_non_empty_string(source, "url", context)
        supports = require_non_empty_string(source, "supports", context)
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"{context}.url must be an HTTP(S) URL.")
        normalized_sources.append({"title": title, "url": url, "supports": supports})

    return normalized_sources


def normalize_source_reference(value: Any) -> Dict[str, Any]:
    """Keep a compact audit reference back to the selected spreadsheet row."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("script_plan.source_reference must be an object.")

    allowed_fields = ("workbook", "sheet", "row", "item_no")
    normalized = {}
    for field in allowed_fields:
        field_value = value.get(field)
        if isinstance(field_value, str) and field_value.strip():
            normalized[field] = normalize_text(field_value, f"script_plan.source_reference.{field}")
        elif isinstance(field_value, (int, float)):
            normalized[field] = field_value
    return normalized


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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", block_id):
        raise ValueError(f"{context}.id must be a filename-safe block identifier.")

    narration = require_non_empty_string(block, "narration", context)
    captions = normalize_string_list(block.get("captions", []), "captions", context, deduplicate=False)
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

    normalized = {
        "id": block_id,
        "narration": narration,
        "captions": captions,
        "visual_keywords": visual_keywords,
        "avoid_visuals": avoid_visuals,
    }
    if "narration_segments" in block:
        segments = block["narration_segments"]
        if not isinstance(segments, list) or len(segments) != len(captions):
            raise ValueError(f"{context}.narration_segments must map one speech segment to each caption.")
        segments = [normalize_text(s, context) if isinstance(s, str) else "" for s in segments]
        if not all(segments) or " ".join(segments).split() != narration.split():
            raise ValueError(f"{context}.narration_segments must preserve the complete narration exactly.")
        normalized["narration_segments"] = segments
    return normalized


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

    if len({block["id"].casefold() for block in blocks}) != len(blocks):
        raise ValueError("Block IDs must be unique.")

    narration = build_narration(blocks, script_plan.get("narration"))

    normalized_script = {
        "title": title,
        "language": language,
        "narration": narration,
        "blocks": blocks,
    }
    medical_sources = normalize_medical_sources(script_plan.get("medical_sources"))
    if medical_sources:
        normalized_script["medical_sources"] = medical_sources

    source_reference = normalize_source_reference(script_plan.get("source_reference"))
    if source_reference:
        normalized_script["source_reference"] = source_reference

    return normalized_script


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
