#!/usr/bin/env bash

set -euo pipefail

TOPIC="${1:-infant_nasal_regurgitation}"
if [[ -x "venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="venv/bin/python"
else
  DEFAULT_PYTHON_BIN="python3"
fi
PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"
VERIFIED_DIR="output/${TOPIC}/verified_input"

"${PYTHON_BIN}" src/prepare_multilingual.py \
  --input-dir "input/${TOPIC}" \
  --output-dir "${VERIFIED_DIR}"

"${PYTHON_BIN}" src/main.py \
  --input "${VERIFIED_DIR}/script_plan.ko.json" \
  --output "output/${TOPIC}/ko"

"${PYTHON_BIN}" src/main.py \
  --input "${VERIFIED_DIR}/script_plan.en.json" \
  --output "output/${TOPIC}/en" \
  --reuse-assets-from "output/${TOPIC}/ko"

"${PYTHON_BIN}" src/main.py \
  --input "${VERIFIED_DIR}/script_plan.es.json" \
  --output "output/${TOPIC}/es" \
  --reuse-assets-from "output/${TOPIC}/ko"

"${PYTHON_BIN}" src/prepare_multilingual.py \
  --output-dir "output/${TOPIC}" \
  --verify-rendered

"${PYTHON_BIN}" scripts/review_final_video_medical.py \
  --output-dir "output/${TOPIC}/ko" \
  --output-dir "output/${TOPIC}/en" \
  --output-dir "output/${TOPIC}/es" \
  --report "output/${TOPIC}/medical_video_review.txt" \
  --json-report "output/${TOPIC}/medical_video_review.json" \
  --visual-input video \
  --resume
