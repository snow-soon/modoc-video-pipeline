"""Use Gemini to align and medically verify Korean, English, and Spanish plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from generate_script import normalize_script_plan
from validate_visuals import coerce_score, gemini_json, get_gemini_client, get_review_model


LANGUAGES = ("ko", "en", "es")
DEFAULT_MAX_ROUNDS = 2


def load_plans(input_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load and normalize all required language plans."""
    plans = {}
    for language in LANGUAGES:
        plan_path = input_dir / f"script_plan.{language}.json"
        if not plan_path.exists():
            raise FileNotFoundError(f"Missing multilingual plan: {plan_path}")
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        plans[language] = normalize_script_plan(raw_plan)
    validate_parallel_structure(plans)
    return plans


def validate_parallel_structure(plans: Dict[str, Dict[str, Any]]) -> None:
    """Require the same block IDs and order in every language."""
    reference_ids = [block["id"] for block in plans["ko"]["blocks"]]
    for language in LANGUAGES:
        block_ids = [block["id"] for block in plans[language]["blocks"]]
        if block_ids != reference_ids:
            raise ValueError(
                f"Block IDs for {language} do not match Korean: {block_ids} != {reference_ids}"
            )


def compact_plans(plans: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return the complete fields needed for cross-language review."""
    return {
        language: {
            "title": plan.get("title", ""),
            "language": plan.get("language", ""),
            "medical_sources": plan.get("medical_sources", []),
            "source_reference": plan.get("source_reference", {}),
            "blocks": plan.get("blocks", []),
        }
        for language, plan in plans.items()
    }


def build_revision_prompt(
    plans: Dict[str, Dict[str, Any]],
    previous_findings: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build a source-aware multilingual correction prompt."""
    return (
        "You are a senior pediatric medical editor fluent in Korean, English, and Spanish. Review the three "
        "short-form scripts as one publication set. Correct medical inaccuracies, unsupported certainty, missing "
        "red flags, unnatural language, encoding corruption, and any translation that changes reassurance or "
        "urgency. The authoritative source notes constrain the claims. Keep the same topic, language, block IDs "
        "and order. Keep narration concise, captions short, and visual keywords concrete English stock-footage "
        "queries. Do not add diagnoses, medication doses, or unreferenced thresholds. Return all three complete "
        "corrected scripts even when no change is needed. Return only JSON with this exact shape:\n"
        "{\n"
        '  "status": "approved | needs_revision | blocked",\n'
        '  "score": 5,\n'
        '  "summary": "",\n'
        '  "findings": [{"severity": "critical | major | minor", "language": "", "block_id": "", "issue": "", "recommendation": ""}],\n'
        '  "revised_scripts": {"ko": {}, "en": {}, "es": {}}\n'
        "}\n"
        "Use score 5 for publication-ready, 4 for safe with optional polish, 3 for mandatory revision, and "
        "1-2 for blocked or unsafe. If status is approved and there are no mandatory findings, score must be "
        "4 or 5. Never return approved with score 1-3.\n\n"
        f"PREVIOUS_FINDINGS:\n{json.dumps(previous_findings or [], ensure_ascii=False, indent=2)}\n\n"
        f"SCRIPTS:\n{json.dumps(compact_plans(plans), ensure_ascii=False, indent=2)}"
    )


def build_verification_prompt(plans: Dict[str, Dict[str, Any]]) -> str:
    """Build an independent final equivalence and safety check."""
    return (
        "Independently verify this Korean-English-Spanish pediatric video script set. Do not rewrite it. Check "
        "medical accuracy against the listed source notes, conditional reassurance, proportionate red flags, "
        "natural native wording, exact cross-language meaning, matching block IDs, and captions that do not "
        "introduce stronger claims. Return only JSON:\n"
        "{\n"
        '  "status": "approved | needs_revision | blocked",\n'
        '  "score": 5,\n'
        '  "must_not_publish": false,\n'
        '  "checks": {"medical_equivalence": false, "warnings_equivalent": false, "language_integrity": false, "captions_faithful": false},\n'
        '  "summary": "",\n'
        '  "findings": [{"severity": "critical | major | minor", "language": "", "block_id": "", "issue": "", "recommendation": ""}]\n'
        "}\n"
        "Use score 5 for publication-ready, 4 for safe with optional polish, 3 for mandatory revision, and "
        "1-2 for blocked or unsafe. If status is approved and all checks are true, score must be 4 or 5. "
        "Never return approved with score 1-3.\n\n"
        f"SCRIPTS:\n{json.dumps(compact_plans(plans), ensure_ascii=False, indent=2)}"
    )


def normalize_revised_plans(
    raw_scripts: Any,
    previous_plans: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Validate Gemini output while preserving source provenance."""
    if not isinstance(raw_scripts, dict):
        raise ValueError("Gemini did not return revised_scripts.")

    revised = {}
    for language in LANGUAGES:
        raw_plan = raw_scripts.get(language)
        if not isinstance(raw_plan, dict):
            raise ValueError(f"Gemini did not return a complete {language} script.")
        raw_plan["medical_sources"] = previous_plans[language].get("medical_sources", [])
        raw_plan["source_reference"] = previous_plans[language].get("source_reference", {})
        raw_plan.pop("narration", None)
        revised[language] = normalize_script_plan(raw_plan)

        previous_block_map = {
            block["id"]: block for block in previous_plans[language].get("blocks", [])
        }
        for block in revised[language].get("blocks", []):
            if block["id"] not in previous_block_map:
                raise ValueError(f"Gemini changed a block ID in the {language} script.")
            combined_avoid_visuals = []
            seen = set()
            for item in [
                *previous_block_map[block["id"]].get("avoid_visuals", []),
                *block.get("avoid_visuals", []),
            ]:
                lowered = item.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    combined_avoid_visuals.append(item)
            block["avoid_visuals"] = combined_avoid_visuals

    validate_parallel_structure(revised)
    for language in LANGUAGES:
        if revised[language]["language"] != previous_plans[language]["language"]:
            raise ValueError(f"Gemini changed the declared language for {language}.")
    return revised


def verification_passed(review: Dict[str, Any]) -> bool:
    """Use a conservative multilingual publication gate."""
    checks = review.get("checks") or {}
    return (
        review.get("status") == "approved"
        and review.get("must_not_publish") is not True
        and coerce_score(review.get("score")) >= 4
        and all(checks.get(key) is True for key in (
            "medical_equivalence",
            "warnings_equivalent",
            "language_integrity",
            "captions_faithful",
        ))
    )


def prepare_multilingual_plans(
    input_dir: Path,
    output_dir: Path,
    model: Optional[str] = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> Dict[str, Any]:
    """Correct and independently verify a three-language publication set."""
    plans = load_plans(input_dir)
    client = get_gemini_client()
    resolved_model = get_review_model(model)
    history = []
    previous_findings: List[Dict[str, Any]] = []

    for round_number in range(1, max(1, max_rounds) + 1):
        print(f"Gemini multilingual correction round {round_number}/{max_rounds}")
        revision_review = gemini_json(
            client,
            resolved_model,
            build_revision_prompt(plans, previous_findings),
        )
        plans = normalize_revised_plans(revision_review.get("revised_scripts"), plans)

        print("Gemini multilingual independent verification")
        verification = gemini_json(client, resolved_model, build_verification_prompt(plans))
        history.append(
            {
                "round": round_number,
                "revision_review": revision_review,
                "verification": verification,
            }
        )
        if verification_passed(verification):
            break
        previous_findings = verification.get("findings") or []
    else:
        verification = history[-1]["verification"]

    output_dir.mkdir(parents=True, exist_ok=True)
    for language, plan in plans.items():
        output_path = output_dir / f"script_plan.{language}.json"
        output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "model": resolved_model,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "passed": verification_passed(verification),
        "history": history,
    }
    report_path = output_dir / "multilingual_script_review.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not report["passed"]:
        raise RuntimeError(f"Multilingual Gemini verification failed: {report_path}")
    print(f"Created verified multilingual plans in {output_dir}")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify and align ko/en/es script plans with Gemini.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prepare_multilingual_plans(
        input_dir=args.input_dir.expanduser().resolve(),
        output_dir=args.output_dir.expanduser().resolve(),
        model=args.model,
        max_rounds=args.max_rounds,
    )


if __name__ == "__main__":
    main()
