import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import io

from video_remix.analysis import IncompleteResponse, analyze, cached_analysis, request, sampling_options
from video_remix.performance_assessment import assess_performance
from video_remix.sound_assessment import assess
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, read


class AnalysisTests(unittest.TestCase):
    def test_non_stop_preserves_partial_raw_usage_without_retry_or_success(self):
        for reason in ("MAX_TOKENS", "SAFETY", "RECITATION", "test-secret-unknown-error"):
            payload = {"candidates": [{"content": {"role": "model", "parts": [
                {"thought": True, "text": "hidden thought"}, {"text": "| partial table |"}]},
                "finishReason": reason, "index": 0}],
                "usageMetadata": {"promptTokenCount": 26105, "candidatesTokenCount": 138,
                                  "thoughtsTokenCount": 3358, "totalTokenCount": 29601},
                "modelVersion": "gemini-3-pro-preview"}
            class Response(io.BytesIO):
                status = 200
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                raw = json.dumps(payload).encode()
                with patch("urllib.request.urlopen", return_value=Response(raw)) as fetch:
                    with self.assertRaises(IncompleteResponse) as error:
                        request(path, "https://example.invalid", "gemini-test", "test-secret", {}, {})
                fetch.assert_called_once()
                self.assertEqual((path / "response.raw.json").read_bytes(), raw)
                self.assertEqual((path / "analysis.md").read_text(), "| partial table |")
                run = read(path / "run.json")
                self.assertEqual(run["status"], "incomplete")
                self.assertEqual(run["usage"], payload["usageMetadata"])
                self.assertFalse(run["completion_verified"])
                self.assertNotIn("test-secret", str(error.exception))
                self.assertNotIn("test-secret", (path / "run.json").read_text())

    def test_stop_and_missing_reason_are_distinct_compatible_successes(self):
        for reason in ("STOP", None):
            candidate = {"content": {"parts": [{"text": "complete observation"}]}}
            if reason is not None:
                candidate["finishReason"] = reason
            class Response(io.BytesIO):
                status = 200
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                with patch("urllib.request.urlopen", return_value=Response(json.dumps({"candidates": [candidate]}).encode())):
                    result = request(path, "https://example.invalid", "model", "secret", {}, {})
                self.assertEqual(read(path / "run.json")["status"], "completed")
                self.assertEqual(result["completion_verified"], reason == "STOP")
                self.assertEqual(result["finish_reason"], reason)
                if reason is None:
                    self.assertIn("未验证", result["completion_note"])

    def test_no_candidate_retains_usage_and_is_incomplete(self):
        for extra in ({}, {"candidates": []}, {"candidates": None},
                      {"promptFeedback": {"blockReason": "SAFETY", "blockReasonMessage": "secret-error"}}):
            payload = {**extra, "usageMetadata": {"totalTokenCount": 14}}
            class Response(io.BytesIO):
                status = 200
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())) as fetch:
                    with self.assertRaises(IncompleteResponse):
                        request(path, "https://example.invalid", "model", "secret-error", {}, {})
                fetch.assert_called_once()
                run = read(path / "run.json")
                self.assertEqual(run["status"], "incomplete")
                self.assertEqual(run["finish_reason"], "NO_CANDIDATE")
                self.assertEqual(run["usage"], payload["usageMetadata"])
                self.assertFalse((path / "analysis.md").exists())
                self.assertNotIn("secret-error", (path / "run.json").read_text())

    def test_legacy_completed_cache_checks_raw_reason_and_does_not_rewrite(self):
        for reason in ("MAX_TOKENS", "SAFETY", "STOP", None, "NO_CANDIDATE"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "analysis/old"
                atomic(path / "run.json", {"status": "completed", "request_signature": "same"})
                candidate = {"content": {"parts": [{"text": "partial"}]}}
                if reason is not None:
                    candidate["finishReason"] = reason
                payload = {"candidates": [candidate]} if reason != "NO_CANDIDATE" else {"candidates": []}
                atomic(path / "response.raw.json", payload)
                (path / "analysis.md").write_text("partial")
                before = {p.name: p.read_bytes() for p in path.iterdir()}
                if reason in ("STOP", None):
                    result = cached_analysis(root, "same")
                    self.assertTrue(result["cache_hit"])
                    self.assertEqual(result["completion_verified"], reason == "STOP")
                else:
                    with self.assertRaises(IncompleteResponse):
                        cached_analysis(root, "same")
                self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})

    def test_incomplete_cached_request_requires_explicit_refresh(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "partial"}]}, "finishReason": "MAX_TOKENS"}]}
        class Response(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root, "test")
            media = root / "source.mp4"
            media.write_bytes(b"source")
            add_asset(root, "ref", media, "reference")
            with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
                    "urllib.request.urlopen", side_effect=lambda *a, **k: Response(json.dumps(payload).encode())) as fetch:
                for _ in range(2):
                    with self.assertRaises(IncompleteResponse):
                        analyze(root, "ref", "model", "brief")
                self.assertEqual(fetch.call_count, 1)
                legacy_path = next((root / "analysis").glob("*/run.json"))
                atomic(legacy_path, {**read(legacy_path), "status": "completed"})
                with self.assertRaises(IncompleteResponse):
                    analyze(root, "ref", "model", "brief")
                self.assertEqual(fetch.call_count, 1)
                payload["candidates"][0]["finishReason"] = "STOP"
                result = analyze(root, "ref", "model", "brief", refresh=True)
                self.assertEqual(fetch.call_count, 2)
                self.assertTrue(result["completion_verified"])

    def test_both_assessments_preserve_incomplete_and_do_not_publish_report(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "partial comparison"}]},
                                   "finishReason": "MAX_TOKENS"}], "usageMetadata": {"totalTokenCount": 8192}}
        class Response(io.BytesIO):
            status = 200
        for module, inputs in (("sound_assessment", "audio_inputs"), ("performance_assessment", "video_inputs")):
            prefix = "video_remix." + module
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch(prefix + ".configuration", return_value=("secret", "https://example.invalid")), \
                        patch(prefix + ".sources", return_value={}), patch(prefix + ".frozen_target", return_value={"status": "legacy"}), \
                        patch(prefix + "." + inputs, return_value=[]), \
                        patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())) as fetch:
                    with self.assertRaisesRegex(RuntimeError, "未完整结束"):
                        if module == "sound_assessment":
                            assess(root, "v", "ref", "sound", "brief", "model")
                        else:
                            assess_performance(root, "v", "ref", "brief", "model")
                fetch.assert_called_once()
                run_path = next((root / "analysis").glob("*/run.json"))
                run = read(run_path)
                self.assertEqual(run["status"], "incomplete")
                self.assertEqual(run["requests"], 1)
                self.assertEqual(run["retry"], 0)
                self.assertEqual(run["usage"], payload["usageMetadata"])
                self.assertFalse((run_path.parent / "assessment.md").exists())
                self.assertEqual((run_path.parent / "analysis.md").read_text(), "partial comparison")

    def test_fps_is_sent_and_separates_cache_without_changing_video(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "observed"}]}}]}
        class Response(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root, "sampling")
            media = root / "reference.mp4"
            media.write_bytes(b"unaltered-video")
            add_asset(root, "ref", media, "reference")
            with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
                    "urllib.request.urlopen", side_effect=lambda *a, **k: Response(json.dumps(payload).encode())) as fetch:
                default = analyze(root, "ref", "gemini-test", "brief")
                first = json.loads(fetch.call_args.args[0].data)["contents"][0]["parts"][1]
                self.assertNotIn("videoMetadata", first)
                dense = analyze(root, "ref", "gemini-test", "brief", video_fps=5)
                part = json.loads(fetch.call_args.args[0].data)["contents"][0]["parts"][1]
                self.assertEqual(part["videoMetadata"], {"fps": 5.0})
                self.assertEqual(part["inline_data"], first["inline_data"])
                self.assertNotEqual(dense["analysis"], default["analysis"])
                self.assertEqual(read(dense["run"])["requested_video_fps"], 5)
                cached = analyze(root, "ref", "gemini-test", "brief", video_fps=5.0)
                self.assertTrue(cached["cache_hit"])
                self.assertEqual(cached["analysis"], dense["analysis"])
                self.assertEqual(fetch.call_count, 2)
                analyze(root, "ref", "gemini-test", "brief", video_fps=2)
                self.assertEqual(fetch.call_count, 3)

    def test_invalid_fps_is_rejected_before_api_or_credentials(self):
        for fps in (0, -1, 25, float("nan"), float("inf"), True, "5"):
            with self.subTest(fps=fps), patch("urllib.request.urlopen") as fetch:
                with self.assertRaisesRegex(ValueError, "video-fps"):
                    analyze(Path("/not-read"), "ref", "model", "brief", video_fps=fps)
                fetch.assert_not_called()
        self.assertEqual(sampling_options(0.5), {"videoMetadata": {"fps": 0.5}})

    def test_analysis_cache_and_explicit_refresh_preserve_original(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "first observation"}]}}]}
        class Response(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            initialize(root, "test")
            media = Path(directory) / "source.mp4"
            media.write_bytes(b"test-source")
            add_asset(root, "ref", media, "reference")
            with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
                    "urllib.request.urlopen", side_effect=lambda *a, **k: Response(json.dumps(payload).encode())) as fetch:
                original = analyze(root, "ref", "gemini-test", "preserve dense speech")
                correction = Path(original["analysis"]).with_name("corrections.md")
                correction.write_text("hard cut, not continuous insertion")
                cached = analyze(root, "ref", "gemini-test", "preserve dense speech")
                self.assertTrue(cached["cache_hit"])
                self.assertEqual(cached["analysis"], original["analysis"])
                self.assertEqual(cached["corrections"], str(correction))
                self.assertEqual(fetch.call_count, 1)
                fresh = analyze(root, "ref", "gemini-test", "preserve dense speech", refresh=True)
                self.assertNotEqual(fresh["analysis"], original["analysis"])
                self.assertEqual(fetch.call_count, 2)
                analyze(root, "ref", "gemini-test", "inspect only lighting")
                self.assertEqual(fetch.call_count, 3)

    def test_incomplete_analysis_cache_is_not_reused(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "observation"}]}}]}
        class Response(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            initialize(root, "test")
            media = Path(directory) / "source.mp4"
            media.write_bytes(b"test-source")
            add_asset(root, "ref", media, "reference")
            with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
                    "urllib.request.urlopen", side_effect=lambda *a, **k: Response(json.dumps(payload).encode())) as fetch:
                first = analyze(root, "ref", "gemini-test", "brief")
                Path(first["analysis"]).write_text("")
                second = analyze(root, "ref", "gemini-test", "brief")
                self.assertNotEqual(first["analysis"], second["analysis"])
                self.assertEqual(fetch.call_count, 2)

    def test_bearer_gateway_header_and_no_secret_in_run(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "observations"}]}}]}
        class Response(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.dict("os.environ", {"GEMINI_AUTH_MODE": "bearer"}), patch(
                    "urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())) as fetch:
                request(path, "https://example.invalid", "gemini-test", "test-secret", {}, {})
            headers = dict(fetch.call_args.args[0].header_items())
            self.assertEqual(headers["Authorization"], "Bearer test-secret")
            self.assertNotIn("X-goog-api-key", headers)
            self.assertNotIn("test-secret", (path / "run.json").read_text())

    def test_http_error_keeps_raw_and_failed_run(self):
        raw = b'{"error":"quota exceeded"}'
        error = urllib.error.HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(raw))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("urllib.request.urlopen", side_effect=error), self.assertRaises(RuntimeError):
                request(path, "https://example.invalid", "gemini-test", "test-key", {}, {})
            self.assertEqual((path / "response.raw.json").read_bytes(), raw)
            self.assertEqual(read(path / "run.json")["status"], "failed")
            self.assertNotIn("test-key", (path / "run.json").read_text())

    def test_valid_text_and_usage_written_without_structural_rewrite(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "# Before / After\nActual observation."}]}}],
                   "usageMetadata": {"totalTokenCount": 10}, "modelVersion": "actual-model"}
        class Response(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())):
                result = request(path, "https://example.invalid", "gemini-test", "test-key", {}, {})
            self.assertEqual(Path(result["analysis"]).read_text(), "# Before / After\nActual observation.")
            self.assertEqual(read(path / "run.json")["actual_model"], "actual-model")
