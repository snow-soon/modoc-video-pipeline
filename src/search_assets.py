"""Search Pexels assets using human-authored visual keywords."""

import json
import os
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = OUTPUT_DIR / "script.json"
ASSETS_FILE = OUTPUT_DIR / "assets.json"
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
REQUEST_TIMEOUT = 30


def load_script() -> dict:
    """Read the parsed script JSON."""
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Missing script file: {SCRIPT_FILE}")
    return json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))


def is_keyword_blocked(keyword: str, avoid_visuals: list[str]) -> bool:
    """Skip keywords that violate the block's safety constraints."""
    lowered_keyword = keyword.casefold()
    for unsafe in avoid_visuals:
        unsafe_lower = unsafe.casefold()
        if unsafe_lower in lowered_keyword or lowered_keyword in unsafe_lower:
            return True
    return False


def select_downloadable_video(result: dict) -> Optional[dict]:
    """Pick the largest downloadable mp4 from one Pexels result."""
    video_files = result.get("video_files", [])
    mp4_files = [
        video_file
        for video_file in video_files
        if video_file.get("file_type") == "video/mp4" and video_file.get("link")
    ]
    if not mp4_files:
        return None
    return sorted(mp4_files, key=lambda item: item.get("height") or 0, reverse=True)[0]


def search_one_keyword(headers: dict, keyword: str) -> Optional[dict]:
    """Search Pexels for one keyword and return the first usable result."""
    response = requests.get(
        PEXELS_SEARCH_URL,
        headers=headers,
        params={"query": keyword, "per_page": 5},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    for video in payload.get("videos", []):
        selected_file = select_downloadable_video(video)
        if selected_file:
            return {
                "pexels_page_url": video["url"],
                "download_url": selected_file["link"],
                "width": selected_file.get("width"),
                "height": selected_file.get("height"),
            }
    return None


def search_assets() -> Path:
    """Search one stock asset per block using ordered human-authored keywords."""
    load_dotenv()
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing PEXELS_API_KEY in environment.")

    script = load_script()
    headers = {"Authorization": api_key}
    results = []

    for block in script.get("blocks", []):
        block_id = block["id"]
        tried_keywords = []
        selected_asset = None

        for keyword in block["visual_keywords"]:
            if is_keyword_blocked(keyword, block.get("avoid_visuals", [])):
                continue

            tried_keywords.append(keyword)
            selected_asset = search_one_keyword(headers, keyword)
            if selected_asset:
                results.append(
                    {
                        "block_id": block_id,
                        "selected_keyword": keyword,
                        "all_keywords": block["visual_keywords"],
                        "pexels_page_url": selected_asset["pexels_page_url"],
                        "download_url": selected_asset["download_url"],
                        "width": selected_asset["width"],
                        "height": selected_asset["height"],
                    }
                )
                print(f"Block: {block_id}")
                print(f"Tried keywords: {tried_keywords}")
                print(f"Selected keyword: {keyword}")
                print(
                    "Selected video dimensions: "
                    f"{selected_asset['width']}x{selected_asset['height']}"
                )
                break

        if selected_asset is None:
            print(f"Block: {block_id}")
            print(f"Tried keywords: {tried_keywords}")
            print("Selected keyword: none")
            print("Selected video dimensions: none")

    ASSETS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {ASSETS_FILE}")
    return ASSETS_FILE


def main() -> None:
    """Search assets for all blocks."""
    search_assets()


if __name__ == "__main__":
    main()
