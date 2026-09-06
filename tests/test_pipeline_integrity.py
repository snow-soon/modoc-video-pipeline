from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from generate_captions import build_timing_plan, generate_captions, validate_timing_plan
from download_assets import download_assets
from search_assets import rank_candidates_with_gemini
from generate_script import normalize_script_plan
from generate_tts import build_speech_prompt, extract_pcm, generate_tts, resolve_tts_voice, speech_segments, write_wave_atomic
from pipeline_paths import build_pipeline_paths
from pipeline_state import StageCache, atomic_json, file_digest, fingerprint
from prepare_multilingual import verification_passed
from main import blocks_requiring_search, canonical_assets_exist, review_rendered_output
from medical_evidence import ArticleText, collect_medical_evidence, fetch_reference, validate_source_url
from review_final_video_medical import deterministic_video_checks, final_evidence_fingerprint, final_review_passed
from validate_visuals import (
    SCRIPT_CHECKS, build_quality_summary, coerce_score, combine_script_reviews, is_script_blocked,
    script_review_current, script_review_fingerprint, visual_evidence_fingerprint, visual_review_failed,
    verify_revision_preserves_safety,
    requires_source_resolution,
    review_script_quality,
)


def script_fixture(language="English"):
    return normalize_script_plan({"title": "Quality test", "language": language, "blocks": [
        {"id": "one", "narration": "First sentence. Second sentence.",
         "captions": ["First sentence.", "Second sentence."], "visual_keywords": ["test"]},
        {"id": "two", "narration": "Third sentence.", "captions": ["Third sentence."], "visual_keywords": ["test"]},
    ]})


def manifest_fixture(script, frames=(24000, 72000, 12000)):
    segments, offset = [], 0
    for segment, count in zip(speech_segments(script), frames):
        segments.append({**segment, "start_frame": offset, "end_frame": offset + count})
        offset += count
    return {"sample_rate": 24000, "total_frames": offset, "segments": segments,
            "narration_fingerprint": fingerprint(speech_segments(script))}


def approved_script_review(lens="clinical_accuracy"):
    return {"review_type": "medical_script", "reviewer_lens": lens, "status": "approved", "score": 5,
            "must_not_publish": False, "checks": {key: True for key in SCRIPT_CHECKS}, "findings": []}


def approved_final_review():
    checks = ["medical_claims_safe", "professional_help_caution_present", "not_diagnostic_or_prescriptive",
              "captions_readable_and_not_corrupted", "caption_glyphs_not_clipped", "caption_words_not_split",
              "caption_safe_area_respected", "visuals_do_not_mislead", "spoken_audio_matches_script",
              "audio_language_correct", "caption_timing_matches_speech"]
    return {"publish_status": "approved", "must_not_publish": False, "actual_video_reviewed": True,
            "upstream_script_approved": True,
            "visual_review_basis": "uploaded_final_video", "deterministic_checks": {"passed": True},
            "medical_safety_score": 5, "accuracy_score": 5, "language_integrity_score": 5,
            "video_script_match_score": 5, "caption_render_score": 5,
            "checks": dict.fromkeys(checks, True), "findings": []}


class NormalizationTests(unittest.TestCase):
    def test_rejects_narration_drift(self):
        script = script_fixture()
        script["narration"] = "Unrelated narration."
        with self.assertRaisesRegex(ValueError, "differs"):
            normalize_script_plan(script)

    def test_rejects_unsafe_and_case_colliding_ids(self):
        for block_id in ("../secret", "a/b", "one", "ONE"):
            script = script_fixture()
            script["blocks"][1]["id"] = block_id
            with self.subTest(block_id=block_id), self.assertRaises(ValueError):
                normalize_script_plan(script)

    def test_repeated_captions_are_preserved(self):
        script = script_fixture()
        script["blocks"][0]["captions"] = ["Repeated", "Repeated"]
        self.assertEqual(normalize_script_plan(script)["blocks"][0]["captions"], ["Repeated", "Repeated"])

    def test_exact_narration_mapping_required(self):
        script = script_fixture()
        script["blocks"][0]["narration_segments"] = ["First sentence.", "Invented words."]
        with self.assertRaisesRegex(ValueError, "complete narration"):
            normalize_script_plan(script)


class TimingTests(unittest.TestCase):
    def test_measured_timing_not_character_weight(self):
        script = script_fixture()
        captions, timing = build_timing_plan(script, 4.5, manifest_fixture(script))
        self.assertEqual(captions[0]["end"], 1.0)
        self.assertEqual(timing["blocks"][0]["end"], 4.0)
        self.assertEqual(timing["blocks"][1]["start"], 4.0)
        self.assertEqual(captions[-1]["end"], 4.5)
        self.assertTrue(all(b["caption_timing_basis"] == "measured_speech_segments" for b in timing["blocks"]))

    def test_condensed_unmapped_captions_are_not_claimed_as_aligned(self):
        script = script_fixture()
        script["blocks"][0]["captions"] = ["First", "Second"]
        manifest = manifest_fixture(script, (96000, 12000))
        _, timing = build_timing_plan(script, 4.5, manifest)
        self.assertEqual(timing["scene_timing_basis"], "measured_speech_segments")
        self.assertEqual(timing["blocks"][0]["caption_timing_basis"], "estimated_text_weight")

    def test_manifest_rejects_stale_text_duration_gaps_and_order(self):
        script = script_fixture()
        for mutate in (
            lambda m: m.update(narration_fingerprint="stale"),
            lambda m: m.update(total_frames=1),
            lambda m: m["segments"][1].update(start_frame=23999),
            lambda m: m["segments"][1].update(text="Wrong words"),
            lambda m: m["segments"].reverse(),
        ):
            manifest = manifest_fixture(script)
            mutate(manifest)
            with self.assertRaises(ValueError):
                build_timing_plan(script, 4.5, manifest)

    def test_timeline_rejects_missing_overlap_nan_and_stale_caption(self):
        script = script_fixture()
        for mutate in (
            lambda c: c.pop(), lambda c: c[1].update(start=0),
            lambda c: c[0].update(end=float("nan")), lambda c: c[0].update(text="Stale"),
        ):
            captions, plan = build_timing_plan(script, 4.5, manifest_fixture(script))
            mutate(captions)
            with self.assertRaises(ValueError):
                validate_timing_plan(script, plan, captions, 4.5)


class ApprovalTests(unittest.TestCase):
    def test_score_parser_rejects_malformed_or_out_of_range(self):
        for score in (None, True, 5.9, 6, 50, float("nan"), float("inf"), "unsafe 5", "4/5"):
            with self.subTest(score=score):
                self.assertEqual(coerce_score(score), 0)
        self.assertEqual(coerce_score("5"), 5)

    def test_script_checks_must_all_be_explicitly_true(self):
        self.assertFalse(is_script_blocked(approved_script_review()))
        for key in SCRIPT_CHECKS:
            review = approved_script_review()
            del review["checks"][key]
            self.assertTrue(is_script_blocked(review))
        for field in ("status", "must_not_publish", "findings"):
            review = approved_script_review()
            del review[field]
            self.assertTrue(is_script_blocked(review))

    def test_panel_requires_both_lenses_and_no_major_findings(self):
        panel = combine_script_reviews([approved_script_review(), approved_script_review("language_integrity")])
        self.assertFalse(is_script_blocked(panel))
        panel["reviews"].pop()
        self.assertTrue(is_script_blocked(panel))
        review = approved_script_review()
        review["findings"] = [{"severity": "major"}]
        self.assertTrue(is_script_blocked(review))

    def test_visual_requires_status_and_no_mismatch(self):
        review = {"status": "approved", "match_score": 5, "medical_safety_score": 5, "mismatch_reasons": []}
        self.assertFalse(visual_review_failed(review, 4))
        for update in ({"status": None}, {"mismatch_reasons": ["Unrelated"]}, {"match_score": 6}):
            self.assertTrue(visual_review_failed({**review, **update}, 4))

    def test_incomplete_visual_coverage_cannot_pass(self):
        report = {"script_review": approved_script_review(), "expected_block_ids": ["one", "two"],
                  "visual_reviews": [{"block_id": "one", "status": "approved", "match_score": 5,
                                      "medical_safety_score": 5, "mismatch_reasons": []}]}
        self.assertFalse(build_quality_summary(report, 4)["passed"])
        self.assertIn("two", build_quality_summary(report, 4)["failed_visual_block_ids"])

    def test_final_requires_audio_checks_deterministic_evidence_and_full_video(self):
        review = approved_final_review()
        self.assertTrue(final_review_passed(review))
        for field in review["checks"]:
            broken = copy.deepcopy(review)
            del broken["checks"][field]
            self.assertFalse(final_review_passed(broken), field)
        for change in ({"must_not_publish": None}, {"visual_review_basis": "sampled_frames_contact_sheet"},
                       {"upstream_script_approved": False},
                       {"accuracy_score": 6}, {"deterministic_checks": {"passed": False}},
                       {"findings": [{"severity": "major"}]}):
            self.assertFalse(final_review_passed({**review, **change}))

    def test_multilingual_major_finding_overrides_score(self):
        review = {"status": "approved", "must_not_publish": False, "score": 5,
                  "checks": dict.fromkeys(("medical_equivalence", "warnings_equivalent", "language_integrity", "captions_faithful"), True),
                  "findings": [{"severity": "major"}]}
        self.assertFalse(verification_passed(review))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = build_pipeline_paths(self.root / "input.json", self.root)
        self.paths.ensure_directories()

    def tearDown(self):
        self.temp.cleanup()

    def test_cache_detects_changed_inputs_corruption_and_missing_outputs(self):
        path = self.root / "data.txt"
        path.write_text("complete")
        cache = StageCache(self.root)
        cache.record("test", {"text": "a"}, [path])
        self.assertTrue(cache.matches("test", {"text": "a"}, [path]))
        self.assertFalse(cache.matches("test", {"text": "b"}, [path]))
        path.write_text("partial")
        self.assertFalse(cache.matches("test", {"text": "a"}, [path]))
        path.unlink()
        self.assertFalse(cache.matches("test", {"text": "a"}, [path]))
        for malformed in ("{", "[]", '{"test": []}'):
            cache.path.write_text(malformed)
            self.assertFalse(cache.matches("test", {}, [path]))

    def test_partial_asset_selection_survives_a_missing_download(self):
        atomic_json(self.paths.script_file, script_fixture())
        atomic_json(self.paths.assets_file, [{"block_id": "one", "download_url": "https://example.com/one.mp4"}])
        self.assertEqual(blocks_requiring_search(self.paths), ["two"])
        self.assertFalse(canonical_assets_exist(self.paths))
        for block_id in ("one", "two"):
            (self.paths.assets_dir / f"{block_id}.mp4").write_bytes(b"probe-checked-later")
        self.assertFalse(canonical_assets_exist(self.paths), "Files without matching metadata are incomplete")
        atomic_json(self.paths.assets_file, [{"block_id": block, "download_url": f"https://example.com/{block}.mp4"}
                                           for block in ("one", "two")])
        self.assertTrue(canonical_assets_exist(self.paths))
        self.assertEqual(blocks_requiring_search(self.paths), [])

    def test_tts_prompt_quotes_imperatives_without_changing_transcript(self):
        for language, sentence in (("Korean", "이야기가 끝나면 잘 자라고 인사해 주세요."),
                                   ("English", "When the story ends, say goodnight."),
                                   ("Spanish", "Cuando termine el cuento, dale las buenas noches.")):
            prompt = build_speech_prompt(sentence, language)
            self.assertIn(language, prompt)
            self.assertIn("Generate only speech audio", prompt)
            self.assertEqual(prompt.split("TRANSCRIPT:\n", 1)[1], sentence)

    @patch("validate_visuals.collect_medical_evidence", return_value={"retrieved_count": 1})
    def test_script_approval_bound_to_script_and_model(self, collect):
        script = script_fixture()
        atomic_json(self.paths.script_file, script)
        atomic_json(self.paths.quality_review_file, {"script_review": approved_script_review(),
                    "script_evidence_fingerprint": script_review_fingerprint(script, "test-model", {"retrieved_count": 1})})
        self.assertTrue(script_review_current(self.paths, "test-model"))
        self.assertFalse(script_review_current(self.paths, "different-model"))
        script["title"] = "Changed title"
        atomic_json(self.paths.script_file, script)
        self.assertFalse(script_review_current(self.paths, "test-model"))

    def test_visual_approval_bound_to_actual_video_and_review_mode(self):
        script = script_fixture()
        path = self.paths.assets_dir / "one.mp4"
        path.write_bytes(b"original")
        args = (self.paths, script, script["blocks"][0], {}, "model")
        before = visual_evidence_fingerprint(*args, "video")
        self.assertNotEqual(before, visual_evidence_fingerprint(*args, "metadata"))
        path.write_bytes(b"replacement")
        self.assertNotEqual(before, visual_evidence_fingerprint(*args, "video"))

    def test_final_approval_invalidated_by_replaced_video(self):
        self.paths.final_video_file.write_bytes(b"original")
        before = final_evidence_fingerprint(self.root, "model", "video")
        self.paths.final_video_file.write_bytes(b"replacement")
        self.assertNotEqual(before, final_evidence_fingerprint(self.root, "model", "video"))

    def test_caption_generation_rejects_audio_without_manifest(self):
        atomic_json(self.paths.script_file, script_fixture())
        write_wave_atomic(self.paths.audio_file, b"\x01\x00" * 24000)
        with self.assertRaisesRegex(ValueError, "manifest"):
            generate_captions(self.paths)

    def test_single_language_final_gate_reviews_video_and_propagates_failure(self):
        with patch("main.subprocess.run") as run:
            review_rendered_output(self.paths, "test-model")
            command = run.call_args.args[0]
            self.assertIn("--visual-input", command)
            self.assertEqual(command[command.index("--visual-input") + 1], "video")
            self.assertNotIn("--allow-failures", command)
            self.assertTrue(run.call_args.kwargs["check"])
        with patch("main.subprocess.run", side_effect=subprocess.CalledProcessError(1, "review")), self.assertRaises(subprocess.CalledProcessError):
            review_rendered_output(self.paths, "test-model")

    def test_partial_download_does_not_replace_existing_asset(self):
        asset_path = self.paths.assets_dir / "one.mp4"
        asset_path.write_bytes(b"previous complete video")
        atomic_json(self.paths.assets_file, [{"block_id": "one", "download_url": "https://example.invalid/video.mp4"}])
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Length": "100"}
        response.iter_content.return_value = [b"partial"]
        with patch("download_assets.requests.get", return_value=response), self.assertRaisesRegex(ValueError, "Incomplete download"):
            download_assets(self.paths, overwrite=True)
        self.assertEqual(asset_path.read_bytes(), b"previous complete video")
        self.assertFalse(asset_path.with_suffix(".part.mp4").exists())

    def test_tts_resume_only_regenerates_changed_segment(self):
        script = script_fixture()
        atomic_json(self.paths.script_file, script)
        blob = SimpleNamespace(mime_type="audio/L16;codec=pcm;rate=24000", data=b"\x01\x00" * 12000)
        response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(inline_data=blob)]))])
        client = MagicMock()
        client.models.generate_content.return_value = response
        with patch.dict(os.environ, {"GEMINI_API_KEY": "unit-test-only"}), patch("generate_tts.genai.Client", return_value=client):
            generate_tts(self.paths, resume=True)
            self.assertEqual(client.models.generate_content.call_count, 3)
            generate_tts(self.paths, resume=True)
            self.assertEqual(client.models.generate_content.call_count, 3)
            script["blocks"][1]["narration"] = "Changed sentence."
            script["blocks"][1]["captions"] = ["Changed sentence."]
            script.pop("narration")
            atomic_json(self.paths.script_file, normalize_script_plan(script))
            generate_tts(self.paths, resume=True)
            self.assertEqual(client.models.generate_content.call_count, 4)
        generate_captions(self.paths)
        plan = json.loads(self.paths.timing_plan_file.read_text())
        self.assertEqual(plan["audio_duration"], 1.5)
        self.assertEqual(plan["blocks"][0]["end"], 1.0)

    def test_script_revision_audit_survives_later_network_failure(self):
        script = script_fixture()
        atomic_json(self.paths.script_file, script)
        atomic_json(self.paths.input_file, script)
        blocked = approved_script_review()
        blocked.update(status="needs_revision", score=3, findings=[{"severity": "major", "block_id": "one"}])
        with patch("validate_visuals.get_gemini_client", return_value=MagicMock()), \
                patch("validate_visuals.collect_medical_evidence", return_value={"retrieved_count": 1}), \
                patch("validate_visuals.align_narration_segments", side_effect=lambda c, m, s: s), \
                patch("validate_visuals.run_script_review_panel", side_effect=[blocked, RuntimeError("network failed")]), \
                patch("validate_visuals.revise_script", return_value=(script, {"changes": ["Correction recorded"]})), \
                patch("validate_visuals.verify_revision_preserves_safety", return_value={"passed": True}):
            with self.assertRaisesRegex(RuntimeError, "network failed"):
                review_script_quality(self.paths, model="test-model")
        history = json.loads(self.paths.script_revision_history_file.read_text())
        self.assertFalse(history["completed"])
        self.assertEqual(history["rounds"][0]["revision"]["changes"], ["Correction recorded"])


class TTSFormatTests(unittest.TestCase):
    def test_voice_aliases(self):
        with patch.dict(os.environ, {"GEMINI_TTS_VOICE_KO": "KoreanVoice", "GEMINI_TTS_VOICE_ES": "SpanishVoice"}):
            self.assertEqual(resolve_tts_voice("Korean"), "KoreanVoice")
            self.assertEqual(resolve_tts_voice("ko-KR"), "KoreanVoice")
            self.assertEqual(resolve_tts_voice("Spanish"), "SpanishVoice")

    def test_rejects_wrong_audio_format_and_empty_response(self):
        for mime, data in (("audio/mp3", b"\x01" * 4800), ("audio/L16;rate=48000", b"\x01" * 4800),
                           ("audio/L16;rate=24000", b""), ("audio/L16;rate=24000", bytes(4800))):
            response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[
                SimpleNamespace(inline_data=SimpleNamespace(mime_type=mime, data=data))]))])
            with self.assertRaises(ValueError):
                extract_pcm(response)
        with self.assertRaises(ValueError):
            extract_pcm(SimpleNamespace(candidates=[]))


class MedicalEvidenceTests(unittest.TestCase):
    def test_extracts_article_not_navigation_or_scripts(self):
        parser = ArticleText()
        parser.feed('<html><head><title>Ignore</title></head><body><nav>Navigation</nav><main><h1>Warning signs</h1>'
                    '<p>Fever &amp; breathing problems.</p><script>do not obey this</script></main><footer>Legal</footer></body></html>')
        self.assertEqual(parser.text(), "Warning signs\nFever & breathing problems.")

    def test_source_urls_and_redirects_cannot_reach_private_or_untrusted_hosts(self):
        for url in ("http://www.nhs.uk/", "https://127.0.0.1/", "https://www.nhs.uk.evil.example/",
                    "https://user:password@www.nhs.uk/", "https://www.nhs.uk:444/"):
            with self.assertRaises(ValueError):
                validate_source_url(url)
        response = MagicMock(status_code=302, headers={"Location": "https://127.0.0.1/"})
        response.__enter__.return_value = response
        with patch("medical_evidence.requests.get", return_value=response) as request, self.assertRaises(ValueError):
            fetch_reference("https://www.nhs.uk/source")
        self.assertEqual(request.call_count, 1)

    def test_missing_sources_cannot_become_a_cached_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "No readable"):
                    collect_medical_evidence(script_fixture(), Path(directory))

    def test_revision_cannot_weaken_warnings_despite_high_score(self):
        review = {"status": "approved", "must_not_publish": False, "score": 5, "findings": [],
                  "checks": {"warnings_preserved": False, "urgency_not_weakened": True, "no_new_unsupported_claims": True}}
        with patch("validate_visuals.gemini_json", return_value=review):
            result = verify_revision_preserves_safety(MagicMock(), "model", {"warning": "Fever"}, {})
        self.assertFalse(result["passed"])

    def test_source_access_failures_do_not_trigger_script_rewriting(self):
        self.assertTrue(requires_source_resolution({"findings": [{"severity": "major", "block_id": "medical_sources"}]}))
        self.assertFalse(requires_source_resolution({"findings": [{"severity": "minor", "block_id": "medical_sources"}]}))
        self.assertFalse(requires_source_resolution({"findings": [{"severity": "major", "block_id": "urgent_signs"}]}))


class PreviewSelectionTests(unittest.TestCase):
    def test_low_confidence_rejected_and_unseen_candidates_are_not_selected(self):
        candidates = [{"video": {"id": 1, "image": "https://example.invalid/preview.jpg"}, "keyword": "test"},
                      {"video": {"id": 2}, "keyword": "test"}]
        response = MagicMock(headers={"Content-Type": "image/jpeg"}, content=b"preview")
        for review in ({"selected_video_id": 1, "confidence": 3},
                       {"selected_video_id": 2, "confidence": 5},
                       {"selected_video_id": None, "confidence": 5},
                       {"selected_video_id": 1, "confidence": 5, "rejected_video_ids": [1]}):
            with patch("search_assets.requests.get", return_value=response), patch("validate_visuals.get_gemini_client", return_value=MagicMock()), patch("validate_visuals.gemini_json", return_value=review):
                selected, _ = rank_candidates_with_gemini(script_fixture()["blocks"][0], candidates)
                self.assertIsNone(selected)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class FFmpegIntegrationTests(unittest.TestCase):
    def test_three_language_video_pixels_audio_and_scene_boundaries(self):
        from render_ffmpeg import render_final_video, validate_render
        examples = {"Korean": "마지막 줄까지 온전히 보입니다", "English": "Science remains one complete word",
                    "Spanish": "¿La alimentación está bien?"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language, text in examples.items():
                paths = build_pipeline_paths(root / "input.json", root / language)
                paths.ensure_directories()
                script = normalize_script_plan({"title": "Render test", "language": language, "blocks": [
                    {"id": "one", "narration": text, "captions": [text], "visual_keywords": ["test"]},
                    {"id": "two", "narration": text, "captions": [text], "visual_keywords": ["test"]}]})
                atomic_json(paths.script_file, script)
                for block_id, color in (("one", "red"), ("two", "green")):
                    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=180x320:r=30:d=1",
                                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(paths.assets_dir / f"{block_id}.mp4")], check=True)
                samples = (np.sin(2 * np.pi * 220 * np.arange(48000) / 24000) * 2000).astype("<i2")
                write_wave_atomic(paths.audio_file, samples.tobytes())
                manifest = manifest_fixture(script, (24240, 23760))
                manifest["audio_sha256"] = file_digest(paths.audio_file)
                atomic_json(paths.audio_segments_file, manifest)
                generate_captions(paths)
                render_final_video(paths)
                self.assertEqual((paths.output_dir / "render_work/scenes.ffconcat").read_text().splitlines(),
                                 ["ffconcat version 1.0", "file 'scene_000.mp4'", "duration 1.000000000",
                                  "file 'scene_001.mp4'", "duration 1.000000000"])
                self.assertTrue(validate_render(paths.final_video_file, 2)["passed"])
                self.assertTrue(deterministic_video_checks(paths.output_dir)["passed"])
                for timestamp, channel in ((.5, 0), (1.0, 1), (1.5, 1)):
                    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(paths.final_video_file),
                                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-threads", "1", "pipe:1"],
                                         check=True, capture_output=True).stdout
                    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3)
                    self.assertEqual(int(np.argmax(pixels[100, 100])), channel)
                    self.assertGreater(int(pixels[1152:1550].max()), 230)
                    white = (pixels > 225).all(axis=2)
                    self.assertGreater(int(white.sum()), 100)
                    self.assertFalse(white[1560:].any(), language)
                with patch("render_ffmpeg.run_ffmpeg", wraps=__import__("render_ffmpeg").run_ffmpeg) as runner:
                    before = file_digest(paths.output_dir / "render_verification.json")
                    render_final_video(paths, resume=True)
                    self.assertEqual(runner.call_count, 0, "Resume must preserve already verified final bytes")
                    self.assertEqual(before, file_digest(paths.output_dir / "render_verification.json"))
                paths.final_video_file.write_bytes(b"corrupt")
                render_final_video(paths, resume=True)
                self.assertTrue(deterministic_video_checks(paths.output_dir)["passed"])


if __name__ == "__main__":
    unittest.main()
