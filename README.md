# Modoc Video Pipeline

AI-powered semi-automated short-form video generation pipeline for medical Q&A content.

## Overview

This project converts medically reviewed Q&A content into a short-form vertical video using Large Language Models (LLMs), Text-to-Speech (TTS), stock media retrieval, and automated video rendering.

The goal is not full end-to-end automation, but rather a **semi-automated workflow** that significantly reduces manual video production effort.

---

## Current Pipeline

```text
Medical Q&A
    ↓
Gemini Script Generation
    ↓
Structured Scene Planning
    ↓
Gemini TTS Generation
    ↓
Subtitle Generation
    ↓
Pexels Asset Retrieval
    ↓
Stock Video Download
    ↓
MoviePy Rendering
    ↓
final_video.mp4
```

---

## Features

### 1. AI Script Generation

Input medical Q&A content is transformed into:

* Video title
* Narration script
* Scene structure
* Visual search keywords

Generated outputs:

```text
output/script.json
output/narration.txt
```

---

### 2. Gemini TTS Integration

Uses Gemini TTS models to generate narration audio automatically.

Generated output:

```text
output/narration.wav
```

---

### 3. Automatic Subtitle Generation

Creates subtitle files directly from narration text.

Generated output:

```text
output/captions.srt
```

Goals:

* Korean-first narration
* TTS and subtitles use identical source text
* Automatic timing distribution
* Caption-safe layout

---

### 4. Stock Media Retrieval

Uses the Pexels API to:

* Search stock videos
* Match scene keywords
* Retrieve downloadable assets

Generated output:

```text
output/assets.json
```

---

### 5. Asset Download Pipeline

Downloads selected stock footage locally.

Generated output:

```text
assets/scene_1.mp4
assets/scene_2.mp4
...
```

---

### 6. Automated Video Rendering

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

* Gemini 2.5 Flash
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
│   └── medical_qna.txt
│
├── output/
│   ├── script.json
│   ├── narration.txt
│   ├── narration.wav
│   ├── captions.srt
│   ├── assets.json
│   └── final_video.mp4
│
├── assets/
│   ├── scene_1.mp4
│   ├── scene_2.mp4
│   └── ...
│
├── src/
│   ├── generate_script.py
│   ├── generate_tts.py
│   ├── generate_captions.py
│   ├── search_assets.py
│   ├── download_assets.py
│   └── render_video.py
│
└── README.md
```

---

## Current Status

### Implemented

* Gemini script generation
* Gemini TTS integration
* Subtitle generation
* Pexels asset search
* Stock footage download
* Automated video rendering
* End-to-end MVP workflow

### In Progress

* Improved subtitle synchronization
* Better caption layout
* Audio/video timing alignment
* Media quality matching
* Human review workflow

---

## Future Improvements

* Word-level subtitle synchronization
* AI-based asset ranking
* Automated scene timing
* Caption animations
* Human-in-the-loop review tools
* Multi-language video generation
* Production-grade rendering pipeline

```
```
