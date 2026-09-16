from pathlib import Path
import subprocess
import tempfile
import traceback
import unittest
from unittest.mock import patch

from video_remix.dreamina import CommandError, Dreamina, command, failure_details, queue_snapshot, validate_capabilities


class DreaminaTests(unittest.TestCase):
    def test_cli_error_preserves_only_safe_diagnostics(self):
        raw = ('upload resource "private.png": upload image: commit phase, commit byte, '
               'CommitImageUpload: do request, Post "https://private/?token=secret" '
               'context deadline exceeded Cookie: session=credential Authorization: Bearer password')
        response = subprocess.CompletedProcess([], 1, "", raw)
        with patch("video_remix.dreamina.subprocess.run", return_value=response) as run:
            with self.assertRaises(CommandError) as caught:
                command(["dreamina", "multimodal2video", "--prompt", "private prompt"])
        run.assert_called_once()
        self.assertEqual(caught.exception.details, {
            "error_class": "upload_commit_timeout",
            "error_reason": "CLI 报告素材上传确认超时，任务是否提交需另行核实", "cli_exit_code": 1})
        output = str(caught.exception) + str(caught.exception.details)
        for private in ("private", "secret", "credential", "password", "Cookie", "Authorization", "https"):
            self.assertNotIn(private, output)

    def test_process_timeout_suppresses_sensitive_command_and_partial_output(self):
        error = subprocess.TimeoutExpired(["dreamina", "--prompt", "private prompt"], 300,
                                          output=b"upload Cookie: credential", stderr=b"token=secret")
        with patch("video_remix.dreamina.subprocess.run", side_effect=error) as run:
            try:
                command(["dreamina", "multimodal2video"], timeout=300)
            except CommandError as caught:
                output = traceback.format_exc()
                self.assertEqual(caught.details["error_class"], "upload_timeout")
                self.assertEqual(caught.details["cli_timeout_seconds"], 300)
            else:
                self.fail("timeout was not raised")
        run.assert_called_once()
        for private in ("private prompt", "credential", "secret", "Cookie"):
            self.assertNotIn(private, output)

    def test_unknown_error_and_malformed_responses_never_echo_provider_text(self):
        for code, stdout in ((1, "Authorization: Bearer secret"), (0, "token=secret"), (0, '["secret"]')):
            with self.subTest(code=code, stdout=stdout):
                response = subprocess.CompletedProcess([], code, stdout, "Cookie: private")
                with patch("video_remix.dreamina.subprocess.run", return_value=response):
                    with self.assertRaises(CommandError) as caught:
                        command(["dreamina", "multimodal2video"])
                self.assertEqual(caught.exception.details["error_class"],
                                 "cli_exit" if code else "invalid_response")
                self.assertNotIn("secret", str(caught.exception.details))
                self.assertNotIn("private", str(caught.exception.details))

    def test_known_submit_rejection_is_classified_without_raw_message(self):
        response = subprocess.CompletedProcess([], 1, '{"ret":1310}',
                                               "ret=1310 ExceedConcurrencyLimit token=secret")
        with patch("video_remix.dreamina.subprocess.run", return_value=response):
            with self.assertRaises(CommandError) as caught:
                command(["dreamina", "multimodal2video"])
        self.assertEqual(caught.exception.details["error_class"], "concurrency_limit")
        self.assertEqual(caught.exception.details["provider_error_code"], 1310)

    def test_terminal_upload_commit_timeout_keeps_failed_provider_semantics(self):
        details = failure_details({"gen_status": "fail", "fail_reason":
                                   'upload image: CommitImageUpload: Post "https://private/?token=secret" '
                                   'context deadline exceeded'})
        self.assertEqual(details["error_class"], "upload_commit_timeout")
        self.assertNotIn("private", str(details))
        self.assertNotIn("secret", str(details))

    def test_failure_details_use_safe_classification_not_provider_text(self):
        details = failure_details({"gen_status": "fail", "fail_reason":
                                   "generation failed: final generation failed https://host/?token=secret"})
        self.assertEqual(details["error_class"], "generation_failed")
        self.assertNotIn("secret", str(details))
        self.assertNotIn("https", str(details))
        unknown = failure_details({"gen_status": "fail", "fail_reason": "Authorization: Bearer credential"})
        self.assertEqual(unknown["error_class"], "provider_failure")
        self.assertNotIn("credential", str(unknown))
        self.assertEqual(failure_details({"gen_status": "cancelled"})["error_class"], "cancelled")

    def test_queue_fields_are_typed_and_allowlisted(self):
        self.assertIsNone(queue_snapshot({"queue_info": "bad shape"}))
        snapshot = queue_snapshot({"queue_info": {"queue_status": "Queueing", "queue_idx": 42,
                                    "queue_length": 100, "priority": True, "debug_info": "secret"}})
        self.assertEqual(snapshot, {"queue_status": "Queueing", "queue_idx": 42, "queue_length": 100})
        self.assertEqual(queue_snapshot({"queue_info": {"queue_status": "Bearer secret"}}),
                         {"queue_status": "Unknown"})

    def test_argv_keeps_prompt_bytes_and_input_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "a.jpg").write_bytes(b"image")
            (root / "b.mp4").write_bytes(b"video")
            spec = {"prompt": "literal `$NO`\nfinal newline\n", "duration": 7,
                    "ratio": "9:16", "resolution": "720p", "model": "seedance2.0fast_vip",
                    "inputs": [{"asset": "a", "type": "image"}, {"asset": "b", "type": "video"}]}
            with patch("video_remix.dreamina.shutil.which", return_value="dreamina"):
                provider = Dreamina()
            args = provider.arguments(spec, root, {"a": {"path": "a.jpg"}, "b": {"path": "b.mp4"}})
            self.assertEqual(args[2:6], ["--image", str(root / "a.jpg"), "--video", str(root / "b.mp4")])
            self.assertEqual(args[args.index("--prompt") + 1], spec["prompt"])

    def test_invalid_parameters_rejected_before_charge(self):
        base = {"model": "seedance2.0fast_vip", "duration": 7, "resolution": "720p",
                "ratio": "9:16", "inputs": [{"type": "image"}]}
        for change in ({"duration": 30}, {"resolution": "4k"}, {"ratio": "weird"},
                       {"inputs": [{"type": "audio"}]}, {"inputs": [{"type": "image"}] * 10}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_capabilities({**base, **change}, "seedance2.0fast_vip")
