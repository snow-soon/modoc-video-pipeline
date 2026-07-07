#!/usr/bin/env bash

set -euo pipefail

TOPIC="${1:-infant_nasal_regurgitation}"

python3 src/main.py --input "input/${TOPIC}/script_plan.ko.json" --output "output/${TOPIC}/ko"
python3 src/main.py --input "input/${TOPIC}/script_plan.en.json" --output "output/${TOPIC}/en"
python3 src/main.py --input "input/${TOPIC}/script_plan.es.json" --output "output/${TOPIC}/es"
