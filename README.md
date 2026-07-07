# Modoc Video Pipeline

Semi-automated short-form video generation pipeline for human-authored, multi-language medical video scripts.

## Overview

This project converts human-authored script plans into short-form vertical videos using Text-to-Speech (TTS), stock media retrieval, and automated video rendering.

The script plan JSON is the source of truth. The pipeline does not translate or rewrite narration. It only normalizes the authored plan, generates TTS, times captions, searches/downloads assets, and renders the final video.

---

## Current Pipeline

```text
Human-authored script_plan.json
    ↓
Script Normalization
    ↓
Gemini Medical Script Review
    ↓
Gemini TTS Generation
    ↓
Caption + Timing Plan Generation
    ↓
Pexels Asset Candidate Retrieval
    ↓
Stock Video Download
    ↓
Gemini Visual Match Review
    ↓
MoviePy Rendering
    ↓
final_video.mp4
```

---

## Features

### 1. Authored Script Plan Normalization

Input script plans are validated and copied into runtime files:

* Video title
* Language
* Full narration
* Block structure
* Authored captions
* Authored visual keywords
* Authored avoid-visual lists

Generated outputs:

```text
output/script.json
output/narration.txt
```

---

### 2. Gemini Quality Review

Gemini is used as a review gate, not as the script author. It checks:

* Medical safety and unsupported claims
* Language corruption or broken characters
* Whether visual keywords imply stronger medical claims than the narration
* Whether selected/downloaded stock videos match each narration block

Generated output:

```text
output/quality_review.json
```

By default, the main pipeline stops before rendering if Gemini blocks the script or gives a selected video a low match/safety score.

---

### 3. Gemini TTS Integration

Uses Gemini TTS models to generate narration audio from the exact authored narration text.

Generated output:

```text
output/narration.wav
```

---

### 4. Automatic Subtitle Generation

Creates subtitle files and block timing plans directly from authored captions and measured narration duration.

Generated output:

```text
output/captions.srt
output/timing_plan.json
```

Goals:

* Human-written narration remains unchanged
* TTS and subtitles use authored source text
* Block-based timing distribution
* Caption-safe layout

---

### 5. Stock Media Retrieval

Uses the Pexels API to:

* Search multiple stock-video candidates with authored keywords
* Prefer vertical, higher-resolution, reasonably short videos
* Keep each run isolated by output directory
* Save block-keyed asset metadata

Generated output:

```text
output/assets.json
```

---

### 6. Asset Download Pipeline

Downloads selected stock footage locally per language run.

Generated output:

```text
output/<topic>/<lang>/assets/block_1.mp4
output/<topic>/<lang>/assets/block_2.mp4
...
```

---

### 7. Automated Video Rendering

Uses MoviePy to combine:

* Stock footage
* Narration audio
* Captions

into a vertical short-form video.

Generated output:

```text
output/final_video.mp4
```

---

## Tech Stack

### AI / LLM

* Gemini quality review
* Gemini TTS

### Media

* Pexels API
* MoviePy
* FFmpeg

### Language

* Python

---

## Project Structure

```text
modoc-video-pipeline/

├── input/
│   ├── daycare_parent_gi/
│   │   ├── script_plan.ko.json
│   │   ├── script_plan.en.json
│   │   └── script_plan.es.json
│   └── infant_nasal_regurgitation/
│       ├── script_plan.ko.json
│       ├── script_plan.en.json
│       └── script_plan.es.json
│
├── output/
│   └── infant_nasal_regurgitation/
│       ├── ko/
│       │   ├── script.json
│       │   ├── narration.txt
│       │   ├── narration.wav
│       │   ├── captions.srt
│       │   ├── timing_plan.json
│       │   ├── assets.json
│       │   ├── quality_review.json
│       │   ├── final_video.mp4
│       │   └── assets/
│       ├── en/
│       └── es/
│
├── scripts/
│   └── run_all_languages.sh
│
├── src/
│   ├── generate_script.py
│   ├── generate_tts.py
│   ├── generate_captions.py
│   ├── search_assets.py
│   ├── download_assets.py
│   ├── validate_visuals.py
│   ├── render_video.py
│   └── pipeline_paths.py
│
└── README.md
```

---

## Current Status

### Implemented

* Authored script-plan normalization
* Gemini medical script quality review
* Gemini TTS integration
* Caption and timing-plan generation
* Pexels multi-candidate asset search
* Stock footage download
* Gemini visual match review
* Automated video rendering
* Wide, word-safe caption rendering
* Language-specific output isolation

### In Progress

* Human review workflow

---

## Future Improvements

* Word-level subtitle synchronization
* Asset ranking and filtering
* Caption animations
* Production-grade rendering pipeline

## CLI Usage

Run one language:

```bash
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.ko.json --output output/infant_nasal_regurgitation/ko
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.en.json --output output/infant_nasal_regurgitation/en
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es
```

Run all three:

```bash
./scripts/run_all_languages.sh infant_nasal_regurgitation
```

Quality review controls:

```bash
# Default: run Gemini medical review and upload downloaded videos for Gemini visual review.
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es

# Faster/cheaper visual review: use selected asset metadata only.
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es --review-video-mode metadata

# Debug rendering without Gemini quality gates.
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es --skip-quality-review

# Run review after assets already exist.
python3 src/validate_visuals.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es --stage all
```

## Multi-language Example

Each language has its own authored script plan, and outputs are isolated by language under the chosen output directory. Gemini does not translate or rewrite scripts in this flow. The human-written script plan remains the source of truth for Korean, English, and Spanish.

```bash
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.ko.json --output output/infant_nasal_regurgitation/ko
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.en.json --output output/infant_nasal_regurgitation/en
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es
```

```bash
chmod +x scripts/run_all_languages.sh
./scripts/run_all_languages.sh infant_nasal_regurgitation
```

## Script Plan Contract

Each `script_plan.json` should include:

```json
{
  "title": "Neck swelling",
  "language": "Korean",
  "narration": "Optional full narration string",
  "avoid_visuals": ["thermometer close-up"],
  "blocks": [
    {
      "id": "block_1",
      "narration": "Human-written block narration",
      "captions": ["Authored caption 1", "Authored caption 2"],
      "visual_keywords": ["parent checking child neck", "pediatric consultation"],
      "avoid_visuals": ["graphic medical illustration"]
    }
  ]
}
```

## Environment

Required:

```bash
GEMINI_API_KEY=your_gemini_api_key_here
PEXELS_API_KEY=your_pexels_api_key_here
```

Optional quality/rendering controls:

```bash
GEMINI_REVIEW_MODEL=gemini-2.5-flash
GEMINI_QUALITY_REVIEW_MODE=video
GEMINI_QUALITY_MIN_SCORE=4
PEXELS_RESULTS_PER_KEYWORD=5
GEMINI_TTS_VOICE_ES=Kore
```

`GEMINI_QUALITY_REVIEW_MODE=video` uploads the downloaded local MP4 assets to Gemini for visual review. Use `metadata` when you only want a cheaper metadata-level gate.
