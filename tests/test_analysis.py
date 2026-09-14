import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import io

from video_remix.analysis import request
from video_remix.storage import read


class AnalysisTests(unittest.TestCase):
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
