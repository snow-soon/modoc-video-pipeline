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

        results.append({
            "caption": scene["caption"],
            "keyword": keyword,
            "video_url": first_video["url"]
        })

with open(OUTPUT_DIR / "assets.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("✅ assets.json created")