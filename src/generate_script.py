"""Generate a Korean-first short-form script from the source medical Q&A."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai


BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_FILE = BASE_DIR / "input" / "medical_qna.example.txt"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = OUTPUT_DIR / "script.json"


def clean_json_response(content: str) -> str:
    """Strip Markdown code fences from a model response."""
    return content.replace("```json", "").replace("```", "").strip()


def build_prompt(source_text: str) -> str:
    """Create the Gemini prompt for a Korean short-form script."""
    return f"""
다음의 의료 검토 기반 부모 상담 Q&A를 바탕으로 한국어 숏폼 영상 스크립트를 만들어 주세요.

중요 규칙:
- 반드시 한국어로 작성하세요.
- 부모가 듣기 쉬운 자연스러운 한국어 말투를 사용하세요.
- 내레이션은 약 35초~50초 분량의 숏폼 스타일로 작성하세요.
- 원문에 없는 의학적 사실을 추가하지 마세요.
- 마지막에는 "증상이 심해지거나 걱정되면 소아청소년과 진료를 받으세요."와 같은 안전 문구를 꼭 포함하세요.
- scene caption은 짧은 한국어 구절로 작성하세요.
- visual_keyword는 Pexels 검색용이므로 영어로 작성하세요.
- scenes에는 start/end를 넣지 마세요.
- 반드시 유효한 JSON만 반환하세요. 설명, 코드블록, 주석은 금지입니다.

반환 형식:
{{
  "title": "...",
  "narration": "...",
  "scenes": [
    {{
      "caption": "...",
      "visual_keyword": "..."
    }}
  ]
}}

원문 Q&A:
{source_text}
""".strip()


def generate_script() -> dict:
    """Generate the structured Korean script with Gemini and save it."""
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing GEMINI_API_KEY in environment.")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    source_text = INPUT_FILE.read_text(encoding="utf-8").strip()
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=build_prompt(source_text),
    )

    raw_content = response.text or ""
    script = json.loads(clean_json_response(raw_content))

    SCRIPT_FILE.write_text(
        json.dumps(script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Created {SCRIPT_FILE}")
    print(f"Generated {len(script.get('scenes', []))} Korean scenes")
    return script


def main() -> None:
    """Generate and save the Korean script JSON."""
    generate_script()


if __name__ == "__main__":
    main()
