"""Generate Korean narration audio from the exact script narration text."""

import json
import os
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT_FILE = OUTPUT_DIR / "script.json"
NARRATION_TEXT_FILE = OUTPUT_DIR / "narration.txt"
AUDIO_FILE = OUTPUT_DIR / "narration.wav"


def load_script() -> dict:
    """Read the generated script JSON."""
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Missing script file: {SCRIPT_FILE}")

    with SCRIPT_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_narration_text(text: str) -> None:
    """Save the exact narration text that will be sent to TTS."""
    NARRATION_TEXT_FILE.write_text(text, encoding="utf-8")
    print(f"Created {NARRATION_TEXT_FILE}")


def generate_tts() -> Path:
    """Generate Korean TTS audio from the exact narration text."""
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing GEMINI_API_KEY in environment.")

    OUTPUT_DIR.mkdir(exist_ok=True)

    script = load_script()
    narration_text = script["narration"]

    # Save the exact text before sending it to TTS so text and audio stay aligned.
    save_narration_text(narration_text)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=narration_text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Kore"
                    )
                )
            ),
        ),
    )

    audio_data = response.candidates[0].content.parts[0].inline_data.data

    with wave.open(str(AUDIO_FILE), "wb") as wave_file:
        wave_file.setnchannels(1)
        wave_file.setsampwidth(2)
        wave_file.setframerate(24000)
        wave_file.writeframes(audio_data)

    print(f"Created {AUDIO_FILE}")
    return AUDIO_FILE


def main() -> None:
    """Generate Korean narration text and audio."""
    generate_tts()


if __name__ == "__main__":
    main()
