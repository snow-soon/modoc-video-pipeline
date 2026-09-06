from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from generate_script import normalize_script_plan, normalize_text  # noqa: E402
from search_assets import select_downloadable_mp4  # noqa: E402
from validate_visuals import compact_asset_for_visual_review  # noqa: E402
from render_video import (  # noqa: E402
    CAPTION_BOTTOM_MARGIN,
    CAPTION_VERTICAL_PADDING_BOTTOM,
    CompositeVideoClip,
    ColorClip,
    VIDEO_HEIGHT,
    VIDEO_SIZE,
    caption_tokens_preserved,
    make_caption_clip,
    render_caption_image,
)


class CaptionRenderingTests(unittest.TestCase):
    def test_whitespace_wrapping_never_splits_an_english_word(self) -> None:
        text = "Science should remain one complete word after responsive caption wrapping"
        _, metadata = render_caption_image(text, "English")

        self.assertTrue(caption_tokens_preserved(text, metadata["wrapped_text"]))
        self.assertNotIn("scien\nce", metadata["wrapped_text"].lower())
        self.assertTrue(metadata["word_tokens_preserved"])

    def test_spanish_accents_render_with_explicit_bottom_padding(self) -> None:
        text = "La alimentación y la respiración están bien"
        image, metadata = render_caption_image(text, "Spanish")

        self.assertGreater(image.height, 0)
        self.assertGreaterEqual(
            metadata["padding"]["bottom"],
            CAPTION_VERTICAL_PADDING_BOTTOM - 2,
        )
        self.assertIn("alimentación", metadata["wrapped_text"])

    def test_caption_screen_bbox_keeps_bottom_safe_area(self) -> None:
        clip, metadata = make_caption_clip(
            "Get local emergency medical help now",
            0.0,
            2.0,
            "English",
        )
        try:
            self.assertGreaterEqual(metadata["screen_bottom_margin"], CAPTION_BOTTOM_MARGIN)
            self.assertLessEqual(metadata["screen_bbox"][3], VIDEO_HEIGHT - CAPTION_BOTTOM_MARGIN)
        finally:
            clip.close()

    def test_rgba_caption_composites_over_video(self) -> None:
        background = ColorClip(size=VIDEO_SIZE, color=(230, 230, 230), duration=1.0)
        caption, metadata = make_caption_clip(
            "La respiración está bien",
            0.0,
            1.0,
            "Spanish",
        )
        composite = CompositeVideoClip([background, caption], size=VIDEO_SIZE)
        try:
            frame = composite.get_frame(0.5)
            top = metadata["screen_bbox"][1]
            bottom = metadata["screen_bbox"][3]
            caption_region = frame[top:bottom, :, :]
            self.assertGreater(caption_region.max() - caption_region.min(), 100)
        finally:
            composite.close()
            caption.close()
            background.close()

    def test_multilingual_caption_examples_fit(self) -> None:
        examples = {
            "Korean": [
                "아기의 표정과 움직임을 천천히 살펴보세요",
                "설명이 길어져도 자막의 마지막 줄이 모두 보여야 합니다",
            ],
            "English": [
                "Science should remain one complete word after caption wrapping",
                "Every line stays readable within the bottom safe area",
            ],
            "Spanish": [
                "¿Puedes leer la última línea de los subtítulos?",
                "La alimentación y la respiración están bien",
                "Las palabras completas conservan sus acentos al cambiar de línea",
            ],
        }
        for language, captions in examples.items():
            with self.subTest(language=language):
                normalized = normalize_script_plan({
                    "title": "Caption rendering example",
                    "language": language,
                    "blocks": [{
                        "id": "example",
                        "narration": " ".join(captions),
                        "captions": captions,
                        "visual_keywords": ["parent and infant at home"],
                    }],
                })
                for caption in normalized["blocks"][0]["captions"]:
                    _, metadata = render_caption_image(caption, language)
                    self.assertTrue(metadata["word_tokens_preserved"])
                    self.assertLessEqual(metadata["line_count"], 3)

    def test_mojibake_is_rejected_before_tts(self) -> None:
        with self.assertRaisesRegex(ValueError, "mojibake"):
            normalize_text("La alimentaciÃ³n", "test")

    def test_pexels_selection_prefers_delivery_resolution_over_4k(self) -> None:
        selected = select_downloadable_mp4(
            [
                {
                    "file_type": "video/mp4",
                    "link": "https://example.com/4k.mp4",
                    "width": 2160,
                    "height": 3840,
                },
                {
                    "file_type": "video/mp4",
                    "link": "https://example.com/1080.mp4",
                    "width": 1080,
                    "height": 1920,
                },
            ]
        )

        self.assertEqual(selected["link"], "https://example.com/1080.mp4")

    def test_visual_review_excludes_rejected_candidate_descriptions(self) -> None:
        compact = compact_asset_for_visual_review(
            {
                "block_id": "hook",
                "keyword": "calm infant at home",
                "pexels_video_id": 123,
                "candidate_summary": [{"description": "unrelated rejected candidate"}],
                "gemini_preview_review": {"rejected_video_ids": [456]},
            }
        )

        self.assertEqual(compact["block_id"], "hook")
        self.assertNotIn("candidate_summary", compact)
        self.assertNotIn("gemini_preview_review", compact)


if __name__ == "__main__":
    unittest.main()
