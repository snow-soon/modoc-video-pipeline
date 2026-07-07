"""Search Pexels assets for the authored block plan."""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

from pipeline_paths import PipelinePaths, build_pipeline_paths

load_dotenv()


DEFAULT_RESULTS_PER_KEYWORD = 5
MIN_REASONABLE_DURATION = 4
MAX_REASONABLE_DURATION = 30


def load_script(paths: PipelinePaths) -> dict:
    """Load the normalized script file."""
    if not paths.script_file.exists():
        raise FileNotFoundError(f"Missing script file: {paths.script_file}")

    return json.loads(paths.script_file.read_text(encoding="utf-8"))


def get_search_keywords(block: Dict) -> List[str]:
    """Return keyword candidates in preferred search order."""
    seen = set()
    ordered_keywords = []

    for keyword in block.get("visual_keywords", []):
        lowered = keyword.lower()
        if keyword and lowered not in seen:
            seen.add(lowered)
            ordered_keywords.append(keyword)

    return ordered_keywords


def select_downloadable_mp4(video_files: List[Dict]) -> Optional[Dict]:
    """Choose the best downloadable MP4 file from a Pexels result."""
    mp4_files = [
        video_file
        for video_file in video_files
        if video_file.get("file_type") == "video/mp4" and video_file.get("link")
    ]

    if not mp4_files:
        return None

    return sorted(
        mp4_files,
        key=lambda video_file: video_file.get("height") or 0,
        reverse=True,
    )[0]


def get_results_per_keyword() -> int:
    """Return the configured number of Pexels candidates to inspect per keyword."""
    raw_value = os.getenv("PEXELS_RESULTS_PER_KEYWORD", str(DEFAULT_RESULTS_PER_KEYWORD))
    try:
        return max(1, min(int(raw_value), 15))
    except ValueError:
        return DEFAULT_RESULTS_PER_KEYWORD


def normalize_for_match(text: str) -> str:
    """Normalize free text for coarse metadata matching."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def contains_avoid_term(candidate_text: str, avoid_visuals: List[str]) -> bool:
    """Return whether candidate metadata contains an avoid hint."""
    normalized_candidate = normalize_for_match(candidate_text)
    return any(normalize_for_match(term) in normalized_candidate for term in avoid_visuals if term)


def score_candidate(video: Dict, selected_file: Dict, keyword_index: int, avoid_visuals: List[str]) -> int:
    """Score a Pexels candidate using metadata available before Gemini review."""
    width = selected_file.get("width") or video.get("width") or 0
    height = selected_file.get("height") or video.get("height") or 0
    duration = video.get("duration") or 0
    score = 0

    if height and width:
        aspect_ratio = height / max(width, 1)
        if aspect_ratio >= 1.4:
            score += 400
        elif aspect_ratio >= 1.0:
            score += 200
        else:
            score -= 150

    score += min(int(height or 0), 2160) // 4
    score -= keyword_index * 40

    if MIN_REASONABLE_DURATION <= duration <= MAX_REASONABLE_DURATION:
        score += 120
    elif duration > MAX_REASONABLE_DURATION:
        score -= 60

    candidate_text = " ".join(
        str(video.get(field, "")) for field in ["url", "image", "id", "duration"]
    )
    if contains_avoid_term(candidate_text, avoid_visuals):
        score -= 1000

    return score


def summarize_candidate(video: Dict, selected_file: Dict, keyword: str, score: int) -> Dict:
    """Keep compact metadata for later Gemini quality review."""
    return {
        "video_id": video.get("id"),
        "keyword": keyword,
        "score": score,
        "pexels_page_url": video.get("url"),
        "preview_image": video.get("image"),
        "duration": video.get("duration"),
        "width": selected_file.get("width"),
        "height": selected_file.get("height"),
    }


def search_videos(keyword: str, headers: dict, per_page: int, portrait_only: bool = True) -> dict:
    """Search Pexels for a single keyword."""
    params = {"query": keyword, "per_page": per_page}
    if portrait_only:
        params["orientation"] = "portrait"

    response = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def search_assets(paths: PipelinePaths) -> List[Dict]:
    """Search assets using authored block keywords."""
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing PEXELS_API_KEY in environment.")

    paths.ensure_directories()
    data = load_script(paths)
    headers = {"Authorization": api_key}
    results = []
    per_page = get_results_per_keyword()

    for block_index, block in enumerate(data.get("blocks", []), start=1):
        search_keywords = get_search_keywords(block)
        avoid_visuals = block.get("avoid_visuals", [])
        candidates = []

        for keyword_index, keyword in enumerate(search_keywords):
            result = search_videos(keyword, headers=headers, per_page=per_page)
            videos = result.get("videos") or []

            if not videos:
                result = search_videos(
                    keyword,
                    headers=headers,
                    per_page=per_page,
                    portrait_only=False,
                )
                videos = result.get("videos") or []

            if not videos:
                continue

            for video in videos:
                selected_file = select_downloadable_mp4(video.get("video_files", []))

                if not selected_file:
                    continue

                score = score_candidate(
                    video=video,
                    selected_file=selected_file,
                    keyword_index=keyword_index,
                    avoid_visuals=avoid_visuals,
                )
                candidates.append(
                    {
                        "video": video,
                        "selected_file": selected_file,
                        "keyword": keyword,
                        "score": score,
                    }
                )

        if not candidates:
            print(f"No downloadable asset found for block {block['id']}: {search_keywords}")
            continue

        candidates = sorted(candidates, key=lambda candidate: candidate["score"], reverse=True)
        selected_candidate = candidates[0]
        selected_video = selected_candidate["video"]
        selected_file = selected_candidate["selected_file"]
        selected_keyword = selected_candidate["keyword"]
        selected_result = {
            "block_id": block["id"],
            "block_index": block_index,
            "captions": block.get("captions", []),
            "narration": block.get("narration", ""),
            "keyword": selected_keyword,
            "search_keywords": search_keywords,
            "avoid_visuals": avoid_visuals,
            "pexels_video_id": selected_video.get("id"),
            "pexels_page_url": selected_video.get("url"),
            "preview_image": selected_video.get("image"),
            "duration": selected_video.get("duration"),
            "download_url": selected_file.get("link"),
            "width": selected_file.get("width"),
            "height": selected_file.get("height"),
            "selection_score": selected_candidate["score"],
            "candidates_considered": len(candidates),
            "candidate_summary": [
                summarize_candidate(
                    video=candidate["video"],
                    selected_file=candidate["selected_file"],
                    keyword=candidate["keyword"],
                    score=candidate["score"],
                )
                for candidate in candidates[:5]
            ],
            "gemini_review_status": "pending",
        }

        print(
            f"Block {block['id']}: selected keyword '{selected_keyword}' "
            f"from {len(candidates)} candidates"
        )
        results.append(selected_result)

    paths.assets_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {paths.assets_file}")
    return results


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Search stock assets for one pipeline run.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Search and save stock assets."""
    args = parse_args()
    search_assets(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
