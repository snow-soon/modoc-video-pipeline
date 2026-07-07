"""Generate narration audio from the exact authored script narration text."""

from __future__ import annotations

import argparse
import json
import os
import wave

from dotenv import load_dotenv
from google import genai
from google.genai import types

from pipeline_paths import PipelinePaths, build_pipeline_paths


def load_script(paths: PipelinePaths) -> dict:
    """Read the generated script JSON."""
    if not paths.script_file.exists():
        raise FileNotFoundError(f"Missing script file: {paths.script_file}")

    with paths.script_file.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_narration_text(paths: PipelinePaths, text: str) -> None:
    """Save the exact narration text that will be sent to TTS."""
    paths.narration_text_file.write_text(text, encoding="utf-8")
    print(f"Created {paths.narration_text_file}")


def resolve_tts_voice(language: str) -> str:
    """Resolve a global or language-specific Gemini TTS voice."""
    language_key = (language or "").split("-")[0].upper()
    if language_key:
        language_voice = os.getenv(f"GEMINI_TTS_VOICE_{language_key}")
        if language_voice:
            return language_voice
    return os.getenv("GEMINI_TTS_VOICE", "Kore")


def generate_tts(paths: PipelinePaths) -> str:
    """Generate narration audio from the exact authored narration text."""
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("Missing GEMINI_API_KEY in environment.")

    paths.ensure_directories()

    script = load_script(paths)
    narration_text = script["narration"]
    language = script.get("language", "unknown")
    voice_name = resolve_tts_voice(language)

    # Save the exact text before sending it to TTS so text and audio stay aligned.
    save_narration_text(paths, narration_text)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=narration_text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        ),
    )

    audio_data = response.candidates[0].content.parts[0].inline_data.data

    with wave.open(str(paths.audio_file), "wb") as wave_file:
        wave_file.setnchannels(1)
        wave_file.setsampwidth(2)
        wave_file.setframerate(24000)
        wave_file.writeframes(audio_data)

    print(f"Created {paths.audio_file}")
    print(f"TTS language: {language}")
    print(f"TTS voice: {voice_name}")
    print(f"Narration characters: {len(narration_text)}")
    return str(paths.audio_file)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Generate TTS for one normalized script.")
    parser.add_argument("--input", required=True, help="Path to the authored script_plan.json file.")
    parser.add_argument("--output", required=True, help="Path to the output directory for this run.")
    return parser.parse_args()


def main() -> None:
    """Generate narration text and audio."""
    args = parse_args()
    generate_tts(build_pipeline_paths(args.input, args.output))


if __name__ == "__main__":
    main()
