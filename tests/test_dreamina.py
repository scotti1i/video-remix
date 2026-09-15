from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_remix.dreamina import Dreamina, failure_details, queue_snapshot, validate_capabilities


class DreaminaTests(unittest.TestCase):
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
