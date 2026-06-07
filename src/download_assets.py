"""Download stock assets by block id."""

import json
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_FILE = OUTPUT_DIR / "assets.json"
REQUEST_TIMEOUT = 60


def load_assets() -> list[dict]:
    """Read the selected asset metadata."""
    if not ASSETS_FILE.exists():
        raise FileNotFoundError(f"Missing assets file: {ASSETS_FILE}")
    return json.loads(ASSETS_FILE.read_text(encoding="utf-8"))


def download_assets() -> Path:
    """Download stock videos using block ids as filenames."""
    ASSETS_DIR.mkdir(exist_ok=True)
    assets = load_assets()

    for asset in assets:
        block_id = asset["block_id"]
        download_url = asset.get("download_url")
        output_path = ASSETS_DIR / f"{block_id}.mp4"

        print(f"Downloading block '{block_id}'...")

        if output_path.exists():
            print(f"Already exists: {output_path}")
            continue

        if not download_url:
            print(f"Skipping block '{block_id}': no download_url found")
            continue

        response = requests.get(download_url, stream=True, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        with output_path.open("wb") as output_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    output_file.write(chunk)

        print(f"Saved: {output_path}")

    return ASSETS_DIR


def main() -> None:
    """Download assets listed in output/assets.json."""
    download_assets()


if __name__ == "__main__":
    main()
