import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"

with open(OUTPUT_DIR / "script.json", "r", encoding="utf-8") as f:
    data = json.load(f)

headers = {
    "Authorization": os.getenv("PEXELS_API_KEY")
}

results = []

for scene in data["scenes"]:

    keyword = scene["visual_keyword"]

    response = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params={
            "query": keyword,
            "per_page": 1
        }
    )

    result = response.json()

    if result.get("videos"):
        first_video = result["videos"][0]

        video_files = first_video.get("video_files", [])

        # Prefer vertical-ish or HD mp4 files.
        mp4_files = [
            vf for vf in video_files
            if vf.get("file_type") == "video/mp4" and vf.get("link")
        ]

        if not mp4_files:
            print(f"No downloadable mp4 found for keyword: {keyword}")
            continue

        # Choose the largest height available for better quality.
        selected_file = sorted(
            mp4_files,
            key=lambda vf: vf.get("height") or 0,
            reverse=True
        )[0]

        results.append({
            "caption": scene["caption"],
            "keyword": keyword,
            "pexels_page_url": first_video["url"],
            "download_url": selected_file["link"],
            "width": selected_file.get("width"),
            "height": selected_file.get("height")
        })

with open(OUTPUT_DIR / "assets.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("✅ assets.json created")