# Modoc Video Pipeline

Human-script-first semi-automated short-form video pipeline for Korean medical content.

## Overview

This pipeline treats the human-written script as the source of truth.

The script is finalized by a human first. After that, the pipeline automates:

1. script parsing and validation
2. exact narration assembly
3. Gemini TTS generation
4. caption timing from authored caption blocks
5. Pexels asset search
6. stock video download
7. MoviePy rendering

This keeps medical wording under human control while still automating the repetitive production steps.

## Why This Direction

The previous version gave too much control to AI script generation. That caused:

- inconsistent script quality
- weak visual matching
- awkward caption splitting
- poor subtitle sync
- stale outputs across runs
- scene timing unrelated to narration meaning

The current pipeline improves medical accuracy, visual relevance, and caption quality by making the script plan human-authored.

## Input

Edit the script plan before running:

`input/script_plan.example.json`

Example shape:

```json
{
  "title": "아이 목 뒤가 갑자기 부었을 때 확인할 점",
  "language": "ko",
  "global_avoid_visuals": [
    "thermometer",
    "red thermometer",
    "37 degree thermometer",
    "fever threshold",
    "clinical lymph node diagram",
    "surgery",
    "severe hospital emergency scene"
  ],
  "blocks": [
    {
      "id": "hook",
      "narration": "아이 목 뒤가 갑자기 부었다면 많이 걱정되실 수 있어요.",
      "captions": [
        "아이 목 뒤가 갑자기 부었다면",
        "많이 걱정되실 수 있어요"
      ],
      "visual_keywords": [
        "parent checking child neck",
        "child neck discomfort",
        "parent caring for child"
      ],
      "avoid_visuals": [
        "red thermometer",
        "37 degree fever thermometer",
        "clinical lymph node diagram"
      ]
    }
  ]
}
```

## Pipeline

```text
Human script plan JSON
    ↓
generate_script.py
    ↓
output/script.json
output/narration.txt
    ↓
generate_tts.py
    ↓
output/narration.wav
    ↓
generate_captions.py
    ↓
output/captions.srt
output/timing_plan.json
    ↓
search_assets.py
    ↓
output/assets.json
    ↓
download_assets.py
    ↓
assets/{block_id}.mp4
    ↓
render_video.py
    ↓
output/final_video.mp4
```

## Files

```text
modoc-video-pipeline/
├── input/
│   ├── medical_qna.example.txt
│   └── script_plan.example.json
├── output/
│   ├── script.json
│   ├── narration.txt
│   ├── narration.wav
│   ├── captions.srt
│   ├── timing_plan.json
│   ├── assets.json
│   └── final_video.mp4
├── assets/
│   └── {block_id}.mp4
├── src/
│   ├── generate_script.py
│   ├── generate_tts.py
│   ├── generate_captions.py
│   ├── search_assets.py
│   ├── download_assets.py
│   ├── render_video.py
│   └── main.py
└── README.md
```

## Environment Variables

Create `.env` from `.env.example` and set:

- `GEMINI_API_KEY`
- `PEXELS_API_KEY`

## Install

```bash
pip install -r requirements.txt
```

MoviePy also requires a working FFmpeg installation on the machine.

## Run

Manual step:

1. Edit `input/script_plan.example.json`

Run command:

```bash
python3 src/main.py
```

## Outputs

- `output/script.json`: validated structured script
- `output/narration.txt`: exact text used for TTS
- `output/narration.wav`: Gemini TTS output
- `output/captions.srt`: authored captions with proportional timing
- `output/timing_plan.json`: block-level and caption-level timing data
- `output/assets.json`: selected Pexels assets per block
- `output/final_video.mp4`: rendered final video
