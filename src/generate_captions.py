"""Generate subtitles from the exact Korean narration text."""

import re
import wave
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
NARRATION_TEXT_FILE = OUTPUT_DIR / "narration.txt"
AUDIO_FILE = OUTPUT_DIR / "narration.wav"
CAPTIONS_FILE = OUTPUT_DIR / "captions.srt"

MIN_CHUNK_LENGTH = 12
MAX_CHUNK_LENGTH = 18


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert seconds into SRT timestamp format."""
    total_milliseconds = int(round(seconds * 1000))

    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    secs = (total_milliseconds % 60_000) // 1000
    milliseconds = total_milliseconds % 1000

    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def load_narration_text() -> str:
    """Read the exact narration text used for TTS."""
    if not NARRATION_TEXT_FILE.exists():
        raise FileNotFoundError(f"Missing narration text file: {NARRATION_TEXT_FILE}")

    return NARRATION_TEXT_FILE.read_text(encoding="utf-8").strip()


def get_audio_duration() -> float:
    """Read the WAV duration without requiring MoviePy."""
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Missing narration audio file: {AUDIO_FILE}")

    with wave.open(str(AUDIO_FILE), "rb") as wave_file:
        frame_count = wave_file.getnframes()
        frame_rate = wave_file.getframerate()

    return frame_count / float(frame_rate)


def split_sentences(text: str) -> list[str]:
    """Split narration into sentence-first chunks while preserving text content."""
    normalized_text = re.sub(r"\r\n?", "\n", text)
    raw_parts = re.split(r"(\n+|(?<=[.!?])\s+|(?<=요\.)\s*|(?<=다\.)\s*)", normalized_text)

    sentences = []
    current = ""

    for part in raw_parts:
        if not part:
            continue

        current += part

        if "\n" in part or re.search(r"[.!?]\s*$", part) or re.search(r"(요\.|다\.)\s*$", part):
            cleaned = current.strip()
            if cleaned:
                sentences.append(cleaned)
            current = ""

    if current.strip():
        sentences.append(current.strip())

    return sentences


def split_long_chunk(chunk: str) -> list[str]:
    """Split long Korean text into smaller subtitle chunks."""
    compact_chunk = re.sub(r"\s+", " ", chunk).strip()

    if len(compact_chunk) <= MAX_CHUNK_LENGTH:
        return [compact_chunk]

    words = compact_chunk.split(" ")

    # Prefer word-based grouping first so the text stays natural.
    grouped_chunks = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"

        if len(candidate) <= MAX_CHUNK_LENGTH:
            current = candidate
            continue

        if current:
            grouped_chunks.append(current)
            current = word
        else:
            grouped_chunks.append(word[:MAX_CHUNK_LENGTH])
            current = word[MAX_CHUNK_LENGTH:]

    if current:
        grouped_chunks.append(current)

    final_chunks = []

    for item in grouped_chunks:
        if len(item) <= MAX_CHUNK_LENGTH:
            final_chunks.append(item)
            continue

        start = 0
        while start < len(item):
            final_chunks.append(item[start : start + MAX_CHUNK_LENGTH])
            start += MAX_CHUNK_LENGTH

    # Merge tiny fragments back into the previous chunk when possible.
    merged_chunks = []
    for item in final_chunks:
        if (
            merged_chunks
            and len(item) < MIN_CHUNK_LENGTH
            and len(f"{merged_chunks[-1]} {item}") <= MAX_CHUNK_LENGTH + 4
        ):
            merged_chunks[-1] = f"{merged_chunks[-1]} {item}".strip()
        else:
            merged_chunks.append(item)

    return [chunk.strip() for chunk in merged_chunks if chunk.strip()]


def build_subtitle_chunks(text: str) -> list[str]:
    """Split narration text into subtitle-sized chunks."""
    subtitle_chunks = []

    for sentence in split_sentences(text):
        subtitle_chunks.extend(split_long_chunk(sentence))

    if not subtitle_chunks:
        raise ValueError("Narration text is empty or could not be split into captions.")

    return subtitle_chunks


def distribute_timings(chunks: list[str], audio_duration: float) -> list[dict]:
    """Assign subtitle timings proportionally to text length."""
    weights = [max(len(re.sub(r"\s+", "", chunk)), 1) for chunk in chunks]
    total_weight = sum(weights)

    timings = []
    current_start = 0.0

    for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
        if index == len(chunks):
            current_end = audio_duration
        else:
            current_end = current_start + (audio_duration * weight / total_weight)

        timings.append(
            {
                "index": index,
                "start": current_start,
                "end": current_end,
                "text": chunk,
            }
        )
        current_start = current_end

    return timings


def write_captions_srt(captions: list[dict]) -> None:
    """Save the generated captions in SRT format."""
    lines = []

    for caption in captions:
        lines.append(str(caption["index"]))
        lines.append(
            f"{seconds_to_srt_timestamp(caption['start'])} --> "
            f"{seconds_to_srt_timestamp(caption['end'])}"
        )
        lines.append(caption["text"])
        lines.append("")

    CAPTIONS_FILE.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"Created {CAPTIONS_FILE}")


def generate_captions() -> Path:
    """Generate subtitles from the exact narration text and audio length."""
    narration_text = load_narration_text()
    audio_duration = get_audio_duration()
    subtitle_chunks = build_subtitle_chunks(narration_text)
    caption_timings = distribute_timings(subtitle_chunks, audio_duration)

    print(f"Narration duration: {audio_duration:.2f} seconds")
    print(f"Generated {len(caption_timings)} subtitle chunks")

    write_captions_srt(caption_timings)
    return CAPTIONS_FILE


def main() -> None:
    """Generate SRT captions from narration text and audio."""
    generate_captions()


if __name__ == "__main__":
    main()
