"""Shared path helpers for parameterized pipeline runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelinePaths:
    """Resolve all output locations for one pipeline run."""

    input_file: Path
    output_dir: Path

    @property
    def assets_dir(self) -> Path:
        return self.output_dir / "assets"

    @property
    def script_file(self) -> Path:
        return self.output_dir / "script.json"

    @property
    def original_script_file(self) -> Path:
        return self.output_dir / "script.original.json"

    @property
    def script_revision_history_file(self) -> Path:
        return self.output_dir / "script_revision_history.json"

    @property
    def narration_text_file(self) -> Path:
        return self.output_dir / "narration.txt"

    @property
    def audio_file(self) -> Path:
        return self.output_dir / "narration.wav"

    @property
    def captions_file(self) -> Path:
        return self.output_dir / "captions.srt"

    @property
    def timing_plan_file(self) -> Path:
        return self.output_dir / "timing_plan.json"

    @property
    def assets_file(self) -> Path:
        return self.output_dir / "assets.json"

    @property
    def rejected_assets_file(self) -> Path:
        return self.output_dir / "rejected_assets.json"

    @property
    def final_video_file(self) -> Path:
        return self.output_dir / "final_video.mp4"

    @property
    def caption_layout_file(self) -> Path:
        return self.output_dir / "caption_layout.json"

    @property
    def asset_optimization_file(self) -> Path:
        return self.output_dir / "asset_optimization.json"

    @property
    def final_quality_review_file(self) -> Path:
        return self.output_dir / "final_quality_review.json"

    @property
    def validation_report_file(self) -> Path:
        return self.output_dir / "visual_validation.json"

    @property
    def quality_review_file(self) -> Path:
        return self.output_dir / "quality_review.json"

    def ensure_directories(self) -> None:
        """Create output directories for the run."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)


def build_pipeline_paths(input_path: str, output_path: str) -> PipelinePaths:
    """Construct resolved pipeline paths from CLI arguments."""
    return PipelinePaths(
        input_file=Path(input_path).expanduser().resolve(),
        output_dir=Path(output_path).expanduser().resolve(),
    )
