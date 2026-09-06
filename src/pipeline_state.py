"""Bind reusable artifacts and approvals to the exact inputs that produced them."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def artifact_digests(paths: Iterable[Path]) -> Optional[Dict[str, str]]:
    try:
        return {str(path.resolve()): file_digest(path) for path in paths}
    except OSError:
        return None


class StageCache:
    def __init__(self, output_dir: Path):
        self.path = output_dir / "stage_state.json"

    def matches(self, stage: str, inputs: Any, outputs: Iterable[Path]) -> bool:
        state = read_json(self.path, {})
        record = state.get(stage, {}) if isinstance(state, dict) else {}
        if not isinstance(record, dict):
            return False
        digests = artifact_digests(outputs)
        return bool(digests) and record.get("inputs") == fingerprint(inputs) and record.get("outputs") == digests

    def record(self, stage: str, inputs: Any, outputs: Iterable[Path]) -> None:
        digests = artifact_digests(outputs)
        if not digests:
            raise ValueError(f"Cannot checkpoint incomplete stage: {stage}")
        state = read_json(self.path, {}) or {}
        if not isinstance(state, dict):
            state = {}
        state[stage] = {"inputs": fingerprint(inputs), "outputs": digests}
        atomic_json(self.path, state)
