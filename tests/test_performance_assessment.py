import base64
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from video_remix.cli import parser
from video_remix.performance_assessment import assess_performance
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, digest, read


class Response(io.BytesIO):
    status = 200


class PerformanceAssessmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        initialize(self.root, "performance test")
        source = self.root / "reference.mp4"
        source.write_bytes(b"reference-original")
        add_asset(self.root, "ref", source, "reference")
        self.variant = self.root / "variants" / "v1"
        atomic(self.variant / "spec.json", {"id": "v1"})
        self.raw = self.variant / "raw.mp4"
        self.raw.write_bytes(b"variant-original")
        atomic(self.variant / "run.json", {"phase": "downloaded", "output": "variants/v1/raw.mp4",
                                           "output_sha256": digest(self.raw)})
        self.payload = {"candidates": [{"content": {"parts": [{"text": "差异待人工核实"}]}}],
                        "modelVersion": "test-actual", "usageMetadata": {"totalTokenCount": 5}}
        for target, kwargs in [
            ("os.environ", {"GEMINI_API_KEY": "hidden-key", "GEMINI_AUTH_MODE": "google"}),
        ]:
            environment = patch.dict(target, kwargs)
            environment.start()
            self.addCleanup(environment.stop)
        for target, value in [
            ("video_remix.performance_assessment.revision", "test-rev"),
            ("video_remix.performance_assessment.shutil.which", "/fake/ffprobe"),
            ("video_remix.sound_assessment.subprocess.run", subprocess.CompletedProcess(
                [], 0, '{"streams":[{"codec_type":"video"}],"format":{"duration":"15","format_name":"mov,mp4,m4a,3gp,3g2,mj2"}}', "")),
        ]:
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def invoke(self):
        return assess_performance(self.root, "v1", "ref", "保留揭晓节奏，允许换场景", "gemini-test")

    def test_two_unmodified_full_videos_allow_no_audio(self):
        record = (self.variant / "run.json").read_bytes()
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(self.payload).encode())) as fetch:
            result = self.invoke()
        self.assertEqual(fetch.call_count, 1)
        parts = json.loads(fetch.call_args.args[0].data)["contents"][0]["parts"]
        inputs = [base64.b64decode(part["inline_data"]["data"]) for part in parts if "inline_data" in part]
        self.assertEqual(inputs, [b"reference-original", b"variant-original"])
        run = read(result["run"])
        self.assertEqual(run["focus"], "performance")
        self.assertEqual(run["actual_model"], "test-actual")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["requests"], 1)
        self.assertEqual(run["usage"]["totalTokenCount"], 5)
        self.assertNotIn("hidden-key", Path(result["run"]).read_text())
        self.assertEqual((self.variant / "run.json").read_bytes(), record)
        self.assertIn("不是人工验收", Path(result["assessment"]).read_text())
        media = read(Path(result["run"]).parent / "media.json")
        self.assertEqual(media["reference"]["transformation"], "none")

    def test_missing_probe_does_not_call_api(self):
        with patch("video_remix.performance_assessment.shutil.which", return_value=None), patch(
                "urllib.request.urlopen") as fetch, self.assertRaisesRegex(RuntimeError, "缺少 ffprobe"):
            self.invoke()
        fetch.assert_not_called()
        run = read(next((self.root / "analysis").glob("*/run.json")))
        self.assertEqual(run["requests"], 0)
        self.assertEqual(run["status"], "failed")

    def test_no_video_stream_is_rejected(self):
        with patch("video_remix.sound_assessment.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 0, '{"streams":[{"codec_type":"audio"}]}', "")), patch(
                "urllib.request.urlopen") as fetch, self.assertRaisesRegex(RuntimeError, "不是可读视频"):
            self.invoke()
        fetch.assert_not_called()

    def test_renamed_image_and_cover_art_are_not_full_videos(self):
        for probe in (
                {"streams": [{"codec_type": "video"}], "format": {"format_name": "png_pipe"}},
                {"streams": [{"codec_type": "video", "disposition": {"attached_pic": 1}}],
                 "format": {"format_name": "mp3"}}):
            with patch("video_remix.sound_assessment.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(probe), "")), patch("urllib.request.urlopen") as fetch, self.assertRaisesRegex(
                    RuntimeError, "不是可读视频"):
                self.invoke()
            fetch.assert_not_called()

    def test_mov_reference_keeps_container_mime(self):
        source = self.root / "reference.mov"
        source.write_bytes(b"mov-original")
        add_asset(self.root, "mov", source, "reference")
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(self.payload).encode())) as fetch:
            assess_performance(self.root, "v1", "mov", "保留节奏", "gemini-test")
        parts = json.loads(fetch.call_args.args[0].data)["contents"][0]["parts"]
        mime = [part["inline_data"]["mime_type"] for part in parts if "inline_data" in part]
        self.assertEqual(mime, ["video/quicktime", "video/mp4"])

    def test_oversized_media_stops_without_truncation(self):
        with self.raw.open("r+b") as file:
            file.truncate(41 * 1024 ** 2)
        atomic(self.variant / "run.json", {"phase": "downloaded", "output": "variants/v1/raw.mp4",
                                           "output_sha256": digest(self.raw)})
        with patch("urllib.request.urlopen") as fetch, self.assertRaisesRegex(RuntimeError, "40 MiB"):
            self.invoke()
        fetch.assert_not_called()
        self.assertEqual(self.raw.stat().st_size, 41 * 1024 ** 2)

    def test_failed_api_keeps_raw_response_without_retry(self):
        error = urllib.error.HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(b"quota"))
        with patch("urllib.request.urlopen", side_effect=error) as fetch, self.assertRaises(RuntimeError):
            self.invoke()
        self.assertEqual(fetch.call_count, 1)
        directory = next((self.root / "analysis").iterdir())
        self.assertEqual((directory / "response.raw.json").read_bytes(), b"quota")
        self.assertEqual(read(directory / "run.json")["status"], "failed")

    def test_changed_output_rejected_and_cli_routes_performance(self):
        self.raw.write_bytes(b"changed")
        with patch("urllib.request.urlopen") as fetch, self.assertRaisesRegex(ValueError, "已被修改"):
            self.invoke()
        fetch.assert_not_called()
        args = parser().parse_args(["assess", "v1", "--reference", "ref", "--focus", "performance",
                                    "--brief", "保留揭晓节奏"])
        self.assertEqual(args.focus, "performance")


if __name__ == "__main__":
    unittest.main()
