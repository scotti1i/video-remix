import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import io

from video_remix.analysis import analyze, request
from video_remix.project import add_asset, initialize
from video_remix.storage import read


class AnalysisTests(unittest.TestCase):
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
