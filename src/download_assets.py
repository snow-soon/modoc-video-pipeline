import json
import requests
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

ASSETS_DIR.mkdir(exist_ok=True)

ASSETS_FILE = OUTPUT_DIR / "assets.json"

with open(ASSETS_FILE, "r", encoding="utf-8") as f:
    assets = json.load(f)

for index, asset in enumerate(assets, start=1):
    print(f"Downloading scene {index}...")

    download_url = asset.get("download_url")

    if not download_url:
        print(f"Skipping scene {index}: no download_url found")
        continue

    output_path = ASSETS_DIR / f"scene_{index}.mp4"

    if output_path.exists():
        print(f"Already exists: {output_path}")
        continue

    response = requests.get(download_url, stream=True)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"Saved: {output_path}")