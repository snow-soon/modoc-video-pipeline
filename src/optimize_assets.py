"""Normalize approved stock footage to the vertical delivery format before rendering."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, List

from pipeline_paths import PipelinePaths, build_pipeline_paths


DELIVERY_WIDTH = 1080
DELIVERY_HEIGHT = 1920
ENCODE_PRESET = "veryfast"
ENCODE_CRF = 18


def probe_dimensions(video_path: Path) -> tuple[int, int]:
    """Read the primary video stream dimensions with ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found in {video_path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def next_source_path(asset_path: Path, width: int, height: int) -> Path:
    """Return an unused audit path for the untouched Pexels source."""
    candidate = asset_path.with_name(f"{asset_path.stem}.source-{width}x{height}.mp4")
    suffix = 2
    while candidate.exists():
        candidate = asset_path.with_name(
            f"{asset_path.stem}.source-{width}x{height}-{suffix}.mp4"
        )
        suffix += 1
    return candidate


def normalize_asset(asset_path: Path) -> Dict:
    """Create an exact-size delivery copy while preserving the original source file."""
    source_width, source_height = probe_dimensions(asset_path)
    result = {
        "asset": asset_path.name,
        "source_width": source_width,
        "source_height": source_height,
        "delivery_width": DELIVERY_WIDTH,
        "delivery_height": DELIVERY_HEIGHT,
    }
    if (source_width, source_height) == (DELIVERY_WIDTH, DELIVERY_HEIGHT):
        result["status"] = "already_optimized"
        return result

    temporary_path = asset_path.with_name(f".{asset_path.stem}.normalized.tmp.mp4")
    temporary_path.unlink(missing_ok=True)
    filter_graph = (
        f"scale={DELIVERY_WIDTH}:{DELIVERY_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={DELIVERY_WIDTH}:{DELIVERY_HEIGHT}"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(asset_path),
            "-vf",
            filter_graph,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            ENCODE_PRESET,
            "-crf",
            str(ENCODE_CRF),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary_path),
        ],
        check=True,
    )

    normalized_dimensions = probe_dimensions(temporary_path)
    if normalized_dimensions != (DELIVERY_WIDTH, DELIVERY_HEIGHT):
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Unexpected normalized dimensions for {asset_path}: {normalized_dimensions}"
        )

    source_path = next_source_path(asset_path, source_width, source_height)
    asset_path.rename(source_path)
    try:
        temporary_path.replace(asset_path)
    except Exception:
        source_path.rename(asset_path)
        raise

    result["status"] = "optimized"
    result["preserved_source"] = source_path.name
    return result


def load_block_ids(paths: PipelinePaths) -> List[str]:
    """Return authored block IDs so rejected and superseded clips are not processed."""
    if not paths.script_file.exists():
        raise FileNotFoundError(f"Missing script file: {paths.script_file}")
    script = json.loads(paths.script_file.read_text(encoding="utf-8"))
    return [block["id"] for block in script.get("blocks", []) if block.get("id")]


def update_asset_metadata(paths: PipelinePaths, results: List[Dict]) -> None:
    """Record delivery dimensions without overwriting the original Pexels dimensions."""
    if not paths.assets_file.exists():
        return
    assets = json.loads(paths.assets_file.read_text(encoding="utf-8"))
    result_map = {Path(result["asset"]).stem: result for result in results}
    for asset in assets:
        result = result_map.get(asset.get("block_id", ""))
        if result:
            asset["delivery_width"] = result["delivery_width"]
            asset["delivery_height"] = result["delivery_height"]
            asset["asset_optimization_status"] = result["status"]
    paths.assets_file.write_text(
        json.dumps(assets, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def optimize_assets(paths: PipelinePaths) -> List[Dict]:
    """Normalize all canonical block assets and write an auditable report."""
    paths.ensure_directories()
    results = []
    for block_id in load_block_ids(paths):
        asset_path = paths.assets_dir / f"{block_id}.mp4"
        if not asset_path.exists():
            raise FileNotFoundError(f"Missing canonical asset: {asset_path}")
        print(f"Optimizing delivery asset: {block_id}")
        results.append(normalize_asset(asset_path))

    update_asset_metadata(paths, results)
    paths.asset_optimization_file.write_text(
        json.dumps({"passed": True, "assets": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Created {paths.asset_optimization_file}")
    return results


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Normalize assets before final rendering.")
    parser.add_argument("--input", required=True, help="Path to the authored script plan.")
    parser.add_argument("--output", required=True, help="Path to the pipeline output directory.")
    return parser.parse_args()


def main() -> None:
    """Run asset normalization for one pipeline output."""
    args = parse_args()
    optimize_assets(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
