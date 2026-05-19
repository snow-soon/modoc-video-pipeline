# modoc-video-pipeline

## Project overview

This repository is a minimal GitHub-ready MVP for a semi-automated short-form medical video pipeline. The current version reads a sample medical Q&A text file and generates three starter assets for downstream video production:

- `output/script.json`
- `output/narration.txt`
- `output/captions.srt`

The implementation is intentionally simple, uses only the Python standard library, and avoids external API calls.

## Current MVP pipeline

1. Read `input/medical_qna.example.txt`
2. Build a hardcoded example structured script
3. Save the structured script as JSON
4. Save the narration text
5. Convert scene timings into SRT captions

## Project structure

```text
modoc-video-pipeline/
├── input/
│   └── medical_qna.example.txt
├── output/
│   └── .gitkeep
├── src/
│   └── main.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

## Setup instructions

1. Make sure Python 3.9+ is installed.
2. Optionally create and activate a virtual environment.
3. No package installation is required for the current MVP.
4. If future steps require secrets, copy `.env.example` to `.env` and keep `.env` uncommitted.

## Run command

```bash
python3 src/main.py
```

## Output files

After running the script, the following files are generated in `output/`:

- `script.json`: hardcoded structured script with scene timing, captions, and visual keywords
- `narration.txt`: plain text narration for future TTS
- `captions.srt`: subtitle file generated from scene timings

Generated output files are ignored by Git. Only `output/.gitkeep` should be committed.

## Next steps

- Gemini script generation
- TTS generation
- stock media retrieval
- template-based video rendering
- human review workflow
