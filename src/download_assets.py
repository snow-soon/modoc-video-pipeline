"""Download block-specific video assets for one pipeline run."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import List, Optional

import requests

from pipeline_paths import PipelinePaths, build_pipeline_paths


def valid_video(path: Path) -> bool:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height,duration", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True, timeout=30)
        video = json.loads(result.stdout)["streams"][0]
        duration = float(video.get("duration", 0))
        return video["width"] > 0 and video["height"] > 0 and math.isfinite(duration) and duration > 0
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return False


def load_assets(paths: PipelinePaths) -> list:
    """Load searched asset metadata."""
    if not paths.assets_file.exists():
        raise FileNotFoundError(f"Missing assets file: {paths.assets_file}")

    with paths.assets_file.open("r", encoding="utf-8") as file:
        return json.load(file)


def download_assets(
    paths: PipelinePaths,
    block_ids: Optional[List[str]] = None,
    overwrite: bool = False,
) -> None:
    """Download all or selected assets into the run-specific asset directory."""
    paths.ensure_directories()
    assets = load_assets(paths)
    target_block_ids = set(block_ids or [asset.get("block_id", "") for asset in assets])

    for asset in assets:
        block_id = asset.get("block_id", "unknown_block")
        if block_id not in target_block_ids:
            continue
        print(f"Downloading asset for {block_id}...")

        download_url = asset.get("download_url")
        if not download_url:
            raise ValueError(f"No download_url found for {block_id}")

        output_path = paths.assets_dir / f"{block_id}.mp4"
        if output_path.exists() and not overwrite and valid_video(output_path):
            print(f"Already exists: {output_path}")
            continue

        temporary = output_path.with_suffix(".part.mp4")
        try:
            with requests.get(download_url, stream=True, timeout=60) as response:
                response.raise_for_status()
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            file.write(chunk)
                expected_size = response.headers.get("Content-Length")
                if expected_size and not response.headers.get("Content-Encoding") and temporary.stat().st_size != int(expected_size):
                    raise ValueError(f"Incomplete download for {block_id}")
            if not valid_video(temporary):
                raise ValueError(f"Downloaded asset is not a valid video: {block_id}")
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)

        print(f"Saved: {output_path}")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Download searched assets for one pipeline run.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Download assets for one run."""
    args = parse_args()
    download_assets(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
