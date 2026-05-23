import os
import json
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

BASE_DIR = Path(__file__).resolve().parents[1]

INPUT_FILE = BASE_DIR / "input" / "medical_qna.example.txt"
OUTPUT_DIR = BASE_DIR / "output"

source_text = INPUT_FILE.read_text(encoding="utf-8")

prompt = f"""
Create a 30-second short-form video script from this medically verified parenting Q&A.

Return valid JSON only.

Format:
{{
  "title": "...",
  "narration": "...",
  "scenes": [
    {{
      "start": 0,
      "end": 3,
      "caption": "...",
      "visual_keyword": "..."
    }}
  ]
}}

Q&A:
{source_text}
"""

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
)

content = response.text.strip()

content = (
    content.replace("```json", "")
    .replace("```", "")
    .strip()
)

data = json.loads(content)

with open(OUTPUT_DIR / "script.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Gemini script generated")