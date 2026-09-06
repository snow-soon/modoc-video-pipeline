"""Generate narration audio from the exact authored script narration text."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from pipeline_paths import PipelinePaths, build_pipeline_paths
from pipeline_state import StageCache, atomic_json, file_digest, fingerprint


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
    language_key = {"KOREAN": "KO", "ENGLISH": "EN", "SPANISH": "ES"}.get(language_key, language_key)
    if language_key:
        language_voice = os.getenv(f"GEMINI_TTS_VOICE_{language_key}")
        if language_voice:
            return language_voice
    return os.getenv("GEMINI_TTS_VOICE", "Kore")


def speech_segments(script: dict) -> list[dict]:
    """Only assign caption boundaries when the authored speech mapping is exact."""
    segments = []
    for block in script["blocks"]:
        texts = block.get("narration_segments")
        captions = block.get("captions", [])
        if not texts and " ".join(captions).split() == block["narration"].split():
            texts = captions
        if texts:
            if len(texts) != len(captions) or " ".join(texts).split() != block["narration"].split():
                raise ValueError(f"Invalid narration_segments in {block['id']}")
        for index, text in enumerate(texts or [block["narration"]]):
            segments.append({"block_id": block["id"], "text": text,
                             "caption_index": index if texts else None})
    if " ".join(segment["text"] for segment in segments).split() != script["narration"].split():
        raise ValueError("Narration differs from the speech segments.")
    return segments


def extract_pcm(response) -> bytes:
    """Accept only the documented mono, 16-bit, 24 kHz Gemini PCM response."""
    chunks = []
    candidates = getattr(response, "candidates", None) or []
    parts = getattr(getattr(candidates[0], "content", None), "parts", None) if candidates else []
    for part in parts or []:
        blob = getattr(part, "inline_data", None)
        if not blob:
            continue
        mime = (blob.mime_type or "").lower().replace(" ", "")
        if not mime.startswith("audio/"):
            continue
        if not re.fullmatch(r"audio/(?:l16|pcm);(?:codec=pcm;)?rate=24000(?:;channels=1)?", mime):
            raise ValueError(f"Unsupported TTS audio format: {mime}")
        if blob.data:
            chunks.append(blob.data)
    pcm = b"".join(chunks)
    if len(pcm) < 4800 or len(pcm) % 2 or not any(pcm):
        raise ValueError("TTS returned empty, silent, truncated, or too-short PCM.")
    return pcm


def write_wave_atomic(path: Path, pcm: bytes) -> None:
    temporary = path.with_suffix(".tmp.wav")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(pcm)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def generate_tts(paths: PipelinePaths, resume: bool = False) -> str:
    """Checkpoint speech segments and measure their exact sample boundaries."""
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

    model = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    settings = {"model": model, "voice": voice_name, "language": language,
                "implementation": file_digest(Path(__file__))}
    segments = speech_segments(script)
    cache = StageCache(paths.output_dir)
    inputs = {"segments": segments, "settings": settings}
    outputs = [paths.audio_file, paths.audio_segments_file]
    if resume and cache.matches("tts", inputs, outputs):
        print("Resume: narration and measured segment manifest match current text and voice")
        return str(paths.audio_file)

    paths.audio_segments_dir.mkdir(parents=True, exist_ok=True)
    config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
    )
    client = genai.Client(api_key=api_key)
    audio_chunks = []
    frame_offset = 0
    measured = []
    try:
        for index, segment in enumerate(segments):
            segment_inputs = {"segment": segment, "settings": settings}
            key = fingerprint(segment_inputs)
            path = paths.audio_segments_dir / f"{key}.wav"
            stage = f"tts_segment_{key}"
            if not (resume and cache.matches(stage, segment_inputs, [path])):
                print(f"TTS segment {index + 1}/{len(segments)}: {segment['block_id']}")
                for attempt in range(3):
                    try:
                        response = client.models.generate_content(model=model, contents=segment["text"], config=config)
                        write_wave_atomic(path, extract_pcm(response))
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2 ** attempt)
                cache.record(stage, segment_inputs, [path])
            with wave.open(str(path), "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24000):
                    raise ValueError(f"Invalid cached speech format: {path.name}")
                pcm = audio.readframes(audio.getnframes())
            frames = len(pcm) // 2
            if not frames:
                raise ValueError("Empty speech segment")
            measured.append({**segment, "start_frame": frame_offset, "end_frame": frame_offset + frames,
                             "file": str(path), "sha256": file_digest(path)})
            frame_offset += frames
            audio_chunks.append(pcm)
    finally:
        client.close()

    write_wave_atomic(paths.audio_file, b"".join(audio_chunks))
    atomic_json(paths.audio_segments_file, {
        "version": 1, "sample_rate": 24000, "total_frames": frame_offset,
        "narration_fingerprint": fingerprint(segments), "audio_sha256": file_digest(paths.audio_file),
        "settings": settings, "segments": measured,
    })
    cache.record("tts", inputs, outputs)

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
    parser.add_argument("--resume", action="store_true", help="Reuse only matching completed speech segments.")
    return parser.parse_args()


def main() -> None:
    """Generate narration text and audio."""
    args = parse_args()
    generate_tts(build_pipeline_paths(args.input, args.output), resume=args.resume)


if __name__ == "__main__":
    main()
