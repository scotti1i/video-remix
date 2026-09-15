import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from video_remix.cli import parser
from video_remix.project import add_asset, initialize
from video_remix.sound_assessment import assess
from video_remix.storage import atomic, digest, read


class Response(io.BytesIO):
    status = 200


class SoundAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        initialize(self.root, "sound test")
        source = self.root / "reference.mp4"
        source.write_bytes(b"reference-video")
        add_asset(self.root, "ref", source, "reference")
        self.variant = self.root / "variants" / "v1"
        atomic(self.variant / "spec.json", {"id": "v1"})
        self.raw = self.variant / "raw.mp4"
        self.raw.write_bytes(b"generated-video")
        atomic(self.variant / "run.json", {"phase": "downloaded", "output": "variants/v1/raw.mp4",
                                           "output_sha256": digest(self.raw)})
        self.env = patch.dict("os.environ", {"GEMINI_API_KEY": "private-test-key", "GEMINI_AUTH_MODE": "google"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.payload = {"candidates": [{"content": {"parts": [{"text": "原片 1–2 秒的停顿待复听。"}]}}],
                        "usageMetadata": {"totalTokenCount": 12}, "modelVersion": "actual-test"}

    def fake_media(self, argv, **kwargs):
        if argv[0] == "/fake/ffprobe":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"streams": [
                {"codec_type": "video"}, {"codec_type": "audio", "index": 1, "channels": 2}]}), "")
        Path(argv[-1]).write_bytes(b"RIFF" + b"wav-bytes" * 12)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def invoke(self):
        return assess(self.root, "v1", "ref", "sound", "保留快速随口说话及原有停顿", "gemini-test")

    def run_directory(self):
        return next((self.root / "analysis").iterdir())

    def test_same_request_contains_two_audio_inputs_and_preserves_original(self):
        original = self.raw.read_bytes()
        record = (self.variant / "run.json").read_bytes()
        with patch("video_remix.sound_assessment.revision", return_value="revision-test"), patch(
                "video_remix.sound_assessment.shutil.which", side_effect=lambda name: "/fake/" + name), patch(
                "video_remix.sound_assessment.subprocess.run", side_effect=self.fake_media) as media, patch(
                "urllib.request.urlopen", return_value=Response(json.dumps(self.payload).encode())) as fetch:
            result = self.invoke()
        self.assertEqual(fetch.call_count, 1)
        body = json.loads(fetch.call_args.args[0].data)
        parts = body["contents"][0]["parts"]
        self.assertEqual([p["inline_data"]["mime_type"] for p in parts if "inline_data" in p],
                         ["audio/wav", "audio/wav"])
        self.assertIn("不默认奖励更干净", parts[0]["text"])
        run = read(result["run"])
        self.assertEqual(run["usage"]["totalTokenCount"], 12)
        self.assertEqual(run["engine_revision"], "revision-test")
        self.assertEqual(run["sources"]["variant"]["sha256"], digest(self.raw))
        self.assertEqual(run["retry"], 0)
        self.assertEqual(run["status"], "completed")
        self.assertIn("不是人工验收", Path(result["assessment"]).read_text())
        self.assertEqual(self.raw.read_bytes(), original)
        self.assertEqual((self.variant / "run.json").read_bytes(), record)
        for call in media.call_args_list:
            self.assertNotIn("-af", call.args[0])
            self.assertNotIn("-t", call.args[0])
        self.assertNotIn("private-test-key", Path(result["run"]).read_text())

    def test_missing_audio_saves_diagnostics_without_api_call(self):
        silent = subprocess.CompletedProcess([], 0, '{"streams":[{"codec_type":"video"}]}', "")
        with patch("video_remix.sound_assessment.revision", return_value="test"), patch(
                "video_remix.sound_assessment.shutil.which", return_value="/fake/tool"), patch(
                "video_remix.sound_assessment.subprocess.run", return_value=silent), patch(
                "urllib.request.urlopen") as fetch, self.assertRaisesRegex(RuntimeError, "有音轨"):
            self.invoke()
        fetch.assert_not_called()
        run = read(self.run_directory() / "run.json")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["requests"], 0)
        self.assertTrue((self.run_directory() / "reference-probe.json").exists())

    def test_missing_ffmpeg_stops_and_preserves_run(self):
        with patch("video_remix.sound_assessment.revision", return_value="test"), patch(
                "video_remix.sound_assessment.shutil.which", return_value=None), patch(
                "urllib.request.urlopen") as fetch, self.assertRaisesRegex(RuntimeError, "缺少 ffprobe 或 ffmpeg"):
            self.invoke()
        fetch.assert_not_called()
        self.assertEqual(read(self.run_directory() / "run.json")["requests"], 0)

    def test_media_failure_diagnostic_is_saved_and_secret_redacted(self):
        failure = subprocess.CompletedProcess([], 1, "", "private-test-key decode failure")
        with patch("video_remix.sound_assessment.revision", return_value="test"), patch(
                "video_remix.sound_assessment.shutil.which", side_effect=lambda name: "/fake/" + name), patch(
                "video_remix.sound_assessment.subprocess.run",
                side_effect=lambda argv, **kwargs: self.fake_media(argv) if argv[0] == "/fake/ffprobe" else failure), patch(
                "urllib.request.urlopen") as fetch, self.assertRaises(RuntimeError):
            self.invoke()
        fetch.assert_not_called()
        diagnostic = (self.run_directory() / "reference-extract.json").read_text()
        self.assertIn("decode failure", diagnostic)
        self.assertNotIn("private-test-key", diagnostic)

    def test_api_failure_is_single_attempt_and_keeps_audio_and_response(self):
        raw = b'{"error":"quota"}'
        error = urllib.error.HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(raw))
        with patch("video_remix.sound_assessment.revision", return_value="test"), patch(
                "video_remix.sound_assessment.shutil.which", side_effect=lambda name: "/fake/" + name), patch(
                "video_remix.sound_assessment.subprocess.run", side_effect=self.fake_media), patch(
                "urllib.request.urlopen", side_effect=error) as fetch, self.assertRaises(RuntimeError):
            self.invoke()
        self.assertEqual(fetch.call_count, 1)
        directory = self.run_directory()
        self.assertEqual((directory / "response.raw.json").read_bytes(), raw)
        self.assertTrue((directory / "reference.wav").exists())
        self.assertTrue((directory / "variant.wav").exists())
        self.assertEqual(read(directory / "run.json")["status"], "failed")

    def test_changed_original_or_reference_rejected_before_processing(self):
        self.raw.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "原始视频已被修改"):
            self.invoke()
        self.raw.write_bytes(b"generated-video")
        reference = self.root / read(self.root / "project.json")["assets"]["ref"]["path"]
        reference.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "参考文件已变化"):
            self.invoke()

    def test_output_escape_and_empty_brief_rejected(self):
        with self.assertRaisesRegex(ValueError, "brief"):
            assess(self.root, "v1", "ref", "sound", "   ", "gemini-test")
        atomic(self.variant / "run.json", {"phase": "downloaded", "output": "../outside.mp4"})
        with self.assertRaisesRegex(ValueError, "不存在或逃逸"):
            self.invoke()

    def test_cli_requires_brief_and_only_accepts_sound(self):
        args = parser().parse_args(["assess", "v1", "--reference", "ref", "--focus", "sound",
                                    "--brief", "保留口语节奏", "--model", "gemini-test"])
        self.assertEqual(args.command, "assess")
        self.assertEqual(args.focus, "sound")
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(["assess", "v1", "--reference", "ref", "--focus", "visual", "--brief", "目标"])
