# Modoc Video Pipeline

Short-form multilingual medical video pipeline with Gemini correction gates and rendered-video QA.

## Overview

This project converts human-authored script plans into short-form vertical videos using Text-to-Speech (TTS), stock media retrieval, and automated video rendering.

The authored plans remain unchanged as the audit source. Gemini writes verified copies under the topic output, then two independent reviewers can correct the runtime script before TTS. Every correction is recorded.

---

## Current Pipeline

```text
Korean / English / Spanish script plans
    ↓
Gemini multilingual equivalence review + correction
    ↓
Independent Gemini clinical + language reviews
    ↓
Automatic script correction and re-review
    ↓
Exact caption/speech mapping + checkpointed Gemini TTS segments
    ↓
Caption + Timing Plan Generation
    ↓
Pexels Asset Candidate Retrieval
    ↓
Stock Video Download
    ↓
Normalize to 1080x1920, then Gemini full-clip visual match review
    ↓
Automatic failed-footage replacement and re-review
    ↓
FFmpeg scene rendering with Pillow caption images
    ↓
Deterministic caption layout QA
    ↓
Post-correction Korean/English/Spanish equivalence review
    ↓
Gemini final MP4 audio + every-caption contact-sheet review
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

### 2. Gemini Quality Review And Correction

Gemini is used as a bounded review-and-correction gate. It checks:

* Medical safety and unsupported claims
* Language corruption or broken characters
* Whether visual keywords imply stronger medical claims than the narration
* Whether selected/downloaded stock videos match each narration block
* Whether all three languages preserve the same reassurance and warning level
* Whether every rendered caption is intact and inside the safe area

Generated output:

```text
output/quality_review.json
output/script.original.json
output/script_revision_history.json
output/caption_layout.json
output/final_quality_review.json
```

By default, mandatory script findings are corrected and reviewed again up to two times. Failed stock footage is replaced and reviewed again up to three times. The run blocks if those retries still fail.
Before medical review, the pipeline retrieves HTML articles from a bounded
allowlist of authoritative domains, validates every redirect, and stores dated
text snapshots and content fingerprints in `medical_evidence.json`. Source
summaries alone are not treated as complete evidence. Unavailable references are
reported; no readable references means the run stops. Snapshots expire after 24 hours.

Every automatic correction also receives an independent before/after safety
comparison. Deleted warnings, weakened urgency, changed AND/OR logic, and new
unsupported claims block the correction. A warning must not be deleted merely
because a short source note omits it. Disputed precautions need clinician review.
Quality reviews now default to `gemini-2.5-pro`; TTS remains configurable
separately. This costs more than Flash review. Both clinical and language passes
are separate calls to the same model, not independent human clinical opinions.

---

### 3. Gemini TTS Integration

Uses Gemini TTS models to generate narration audio from the exact authored narration text.

Speech is generated in checkpointed segments. The clinical/language review stage
first maps each caption to an exact, consecutive portion of the narration without
rewriting it. Each WAV segment's sample count determines its caption and scene
boundaries. `GEMINI_TTS_MODEL` and per-language voices are configurable.

Generated output:

```text
output/narration.wav
output/audio_segments.json
output/audio_segments/
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
* Measured sample-based scene and caption boundaries
* Caption-safe layout
* Explicit bottom glyph padding and a 360-pixel platform-safe bottom margin
* Whitespace-only line wrapping so an English or Spanish word is never split

---

### 5. Stock Media Retrieval

Uses the Pexels API to:

* Search multiple stock-video candidates with authored keywords
* Prefer vertical, higher-resolution, reasonably short videos
* Keep each run isolated by output directory
* Save block-keyed asset metadata
* Deduplicate preview candidates and reject confidence below 4/5
* Stop when no acceptable preview is available; never silently select a rejected candidate

Generated output:

```text
output/assets.json
```

---

### 6. Asset Download Pipeline

Downloads selected stock footage locally per language run.
Downloads use temporary files, content-length checks, and FFprobe validation before
replacing a canonical clip. Vertical cropping happens before full-video review,
so Gemini inspects the framing actually used in the render.

Generated output:

```text
output/<topic>/<lang>/assets/block_1.mp4
output/<topic>/<lang>/assets/block_2.mp4
...
```

---

### 7. Automated Video Rendering

Uses native FFmpeg encoding and Pillow-rendered caption images to combine:

* Stock footage
* Narration audio
* Captions

into a vertical short-form video.
Scenes are independently checkpointed, keeping memory bounded and allowing
unchanged scenes to be reused. FFprobe checks dimensions, frame rate, and
audio/video duration before atomically replacing the final MP4.

Generated output:

```text
output/final_video.mp4
output/render_verification.json
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
* Pillow-based caption rendering with deterministic glyph bounds
* Automatic script correction and stock-footage replacement loops
* Final MP4 and every-caption Gemini publication gate
* Language-specific output isolation

### Still Required For Publication

* Human clinician sign-off for production medical content

---

## Future Improvements

* Independent word-level ASR/forced alignment (current timing is per speech segment)
* Human clinician verification of cited sources and disputed warnings
* Caption animations
* Automatic repair of final audio/visual findings (current final gate blocks publication)

## CLI Usage

The repository contains source code, tests, configuration examples, and input
format examples. Production script plans, spreadsheets, media, and generated
reports stay local and are excluded by `.gitignore`. Create your own
`input/<topic>/script_plan.ko.json`, `script_plan.en.json`, and
`script_plan.es.json` using `input/script_plan.example.json` before running the
commands below. The topic names in these commands illustrate local content
directories; production plans are not included in the repository.

Run one language:

```bash
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.ko.json --output output/infant_nasal_regurgitation/ko
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.en.json --output output/infant_nasal_regurgitation/en
python3 src/main.py --input input/infant_nasal_regurgitation/script_plan.es.json --output output/infant_nasal_regurgitation/es
```

Single-language runs also invoke the final rendered-video/audio gate and write
`medical_video_review.txt` in that language's output directory. Rendering alone
is not publication approval. The multilingual runner reuses unchanged per-language
final reviews when assembling the combined report.

Run all three:

```bash
./scripts/run_all_languages.sh infant_nasal_regurgitation
```

The multilingual runner performs cross-language correction, generates Korean first,
reuses its visual set for English and Spanish, revalidates each language against
those clips, checks equivalence again after per-language corrections, and writes
the final medical report automatically.

### Reliable Resume And Approval Evidence

Add `--resume` to `src/main.py` after an interruption. Reuse is based on SHA-256
input/output fingerprints, not file existence. Edited narration, voice/model
changes, replaced footage, and changed review policies invalidate affected stages
or approvals. Old runs without evidence manifests must regenerate TTS and timing.
Production inputs and generated artifacts remain local; `stage_state.json` and
all segment WAVs live under the ignored output directory.

Missing checks, invalid scores, major findings, incomplete block coverage, or
failed deterministic checks cannot be overridden by an `approved` model label.
Frame-only final review is diagnostic and cannot pass the publication gate because
it cannot verify actual speech or synchronization.
For diagnostic audits of older videos, the final-review CLI's `--allow-failures`
can write Gemini feedback even when deterministic evidence or upstream script
approval is missing. Such a diagnostic report cannot become publication approval.

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

Each language has its own authored source plan, and outputs are isolated by language. Gemini corrections are written to `output/<topic>/verified_input`; files under `input/<topic>` are not overwritten.

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
  "avoid_visuals": ["thermometer close-up"],
  "blocks": [
    {
      "id": "block_1",
      "narration": "Human-written first sentence. Human-written second sentence.",
      "captions": ["Authored caption 1", "Authored caption 2"],
      "narration_segments": ["Human-written first sentence.", "Human-written second sentence."],
      "visual_keywords": ["parent checking child neck", "pediatric consultation"],
      "avoid_visuals": ["graphic medical illustration"]
    }
  ]
}
```

`narration_segments` is optional for authored input; Gemini creates an exact
mapping during quality review when it is missing. Joining the segments must equal
the block narration, and their count must equal the caption count. An optional
top-level `narration` must match all block narration in order. Block IDs must be
filename-safe and unique, including on case-insensitive filesystems. Debug runs
without quality review may estimate caption timing inside a measured block; this
is explicitly labeled `estimated_text_weight`, not word-level alignment.

## Environment

Required:

```bash
GEMINI_API_KEY=your_gemini_api_key_here
PEXELS_API_KEY=your_pexels_api_key_here
```

Optional quality/rendering controls:

```bash
GEMINI_REVIEW_MODEL=gemini-2.5-pro
GEMINI_QUALITY_REVIEW_MODE=video
GEMINI_QUALITY_MIN_SCORE=4
GEMINI_MAX_SCRIPT_REVISIONS=2
GEMINI_MAX_VISUAL_REPLACEMENTS=3
PEXELS_RESULTS_PER_KEYWORD=5
GEMINI_TTS_VOICE_ES=Kore
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
```

`GEMINI_QUALITY_REVIEW_MODE=video` uploads the downloaded local MP4 assets to Gemini for visual review. Use `metadata` when you only want a cheaper metadata-level gate.

The final review uploads each rendered MP4 plus a contact sheet sampled at the midpoint of every caption. Automated Gemini review is a publication-safety screen, not a clinician sign-off.
The final gate checks the actual spoken audio, language, and caption timing in
addition to medical and visual content. Source notes are authored references, not
complete evidence; the reviewer also receives dated, fetched article snapshots.
A 5/5 model score is not a measured
medical error rate or a guarantee of safety.

## Tests

```bash
venv/bin/python -m unittest discover -s tests -v
```

Tests use no external APIs or private production inputs. FFmpeg integration tests
render all three languages and inspect output pixels, scene boundaries, audio,
safe areas, and scene-cache reuse. FFmpeg/FFprobe must be installed for those tests.
