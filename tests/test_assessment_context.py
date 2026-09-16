import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_remix.creative import assemble, store_plan
from video_remix.performance_assessment import assess_performance, PROMPT as PERFORMANCE_PROMPT
from video_remix.project import add_asset, initialize
from video_remix.sound_assessment import assess, PROMPT as SOUND_PROMPT
from video_remix.storage import atomic, digest, read


class Response(io.BytesIO):
    status = 200


class AssessmentTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize(self.root, "frozen target")
        source = self.root / "reference.mp4"
        source.write_bytes(b"reference")
        add_asset(self.root, "ref", source, "reference")
        picture = self.root / "product.png"
        picture.write_bytes(b"product")
        add_asset(self.root, "product", picture, "product")
        self.plan = {
            "format": "video-remix-plan.v1", "id": "v1", "mode": "image_text",
            "kind": "replica", "provider": "dreamina", "model": "seedance2.0fast_vip",
            "duration": 15, "ratio": "9:16", "resolution": "720p",
            "inputs": [{"type": "image", "asset": "product", "role": "product identity"}],
            "intent": "private unrelated plan content", "product": "unchanged",
            "look": "same scene", "sound": "preserve low close voice",
            "shots": [{"start": 0, "end": 15, "action": "two complete cycles"}],
            "provenance": {"target": "保留低沉近讲，两次变暗及恢复，只允许换穿搭",
                           "source_to_output": [{"source": [3, 18], "output": [0, 15],
                                                 "reason": "保留完整演示"}],
                           "private_notes": "/Users/private/not-for-upload"}}
        store_plan(self.root, self.plan)
        self.plan_path = self.root / "plans/v1/plan.json"
        self.spec_path = self.root / "variants/v1/spec.json"
        raw = self.root / "variants/v1/raw.mp4"
        raw.write_bytes(b"generated-video")
        self.run_path = raw.parent / "run.json"
        atomic(self.run_path, {"phase": "downloaded", "output": "variants/v1/raw.mp4",
                              "output_sha256": digest(raw), "spec_sha256": digest(self.spec_path)})
        for target, kwargs in [
            ("video_remix.sound_assessment.audio_inputs", {"return_value": []}),
            ("video_remix.performance_assessment.video_inputs", {"return_value": []}),
            ("video_remix.sound_assessment.revision", {"return_value": "test"}),
            ("video_remix.performance_assessment.revision", {"return_value": "test"})]:
            mocked = patch(target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        environment = patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "GEMINI_AUTH_MODE": "google"})
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, mode, brief="这次只看动作，允许声音变轻快"):
        if mode == "sound":
            return assess(self.root, "v1", "ref", mode, brief, "gemini-test")
        return assess_performance(self.root, "v1", "ref", brief, "gemini-test")

    def capture(self, mode):
        payload = {"candidates": [{"content": {"parts": [{"text": "辅助意见"}]}}]}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())) as fetch:
            result = self.invoke(mode)
        self.assertEqual(fetch.call_count, 1)
        prompt = json.loads(fetch.call_args.args[0].data)["contents"][0]["parts"][0]["text"]
        return prompt, read(result["run"])

    def test_both_modes_send_original_target_and_mapping_not_whole_plan(self):
        for mode in ("sound", "performance"):
            with self.subTest(mode=mode):
                prompt, run = self.capture(mode)
                self.assertIn(self.plan["provenance"]["target"], prompt)
                self.assertIn('"source": [3, 18]', prompt)
                self.assertIn("brief 只能指定检查焦点，不能扩大允许变化", prompt)
                self.assertIn("不能当作成片已经做到的证据", prompt)
                self.assertIn("允许声音变轻快", prompt)
                self.assertNotIn(self.plan["intent"], prompt)
                self.assertNotIn("/Users/private", prompt)
                self.assertNotIn(str(self.root), prompt)
                context = run["target_context"]
                self.assertEqual(context["fields"], {key: self.plan["provenance"][key]
                                                    for key in ("target", "source_to_output")})
                self.assertEqual(context["plan_sha256"], digest(self.plan_path))
                self.assertTrue(context["submitted_spec_verified"])
                self.assertEqual(run["prompt"], prompt)

    def test_legacy_missing_plan_or_target_keeps_original_prompt(self):
        for has_plan in (True, False):
            self.plan.pop("provenance", None)
            atomic(self.plan_path, self.plan)
            if not has_plan:
                self.plan_path.unlink()
            for mode, base in (("sound", SOUND_PROMPT), ("performance", PERFORMANCE_PROMPT)):
                prompt, run = self.capture(mode)
                self.assertEqual(prompt, base + run["brief"])
                self.assertEqual(run["target_context"]["status"], "legacy")

    def test_changed_plan_cannot_silently_fall_back_to_new_brief(self):
        self.plan["sound"] = "newly changed voice"
        atomic(self.plan_path, self.plan)
        for mode in ("sound", "performance"):
            with patch("urllib.request.urlopen") as fetch, self.assertRaisesRegex(ValueError, "计划与版本规格不一致"):
                self.invoke(mode)
            fetch.assert_not_called()

    def test_matching_edited_plan_and_spec_still_check_submission_hash(self):
        self.plan["sound"] = "new voice"
        atomic(self.plan_path, self.plan)
        atomic(self.spec_path, assemble(self.plan))
        for mode in ("sound", "performance"):
            with patch("urllib.request.urlopen") as fetch, self.assertRaisesRegex(ValueError, "规格与提交记录不一致"):
                self.invoke(mode)
            fetch.assert_not_called()

    def test_old_run_without_spec_hash_is_explicitly_not_verified(self):
        run = read(self.run_path)
        run.pop("spec_sha256")
        atomic(self.run_path, run)
        for mode in ("sound", "performance"):
            _, result = self.capture(mode)
            self.assertFalse(result["target_context"]["submitted_spec_verified"])
            self.assertIn("不能证明目标未被事后单独改写", result["target_context"]["limitation"])

    def test_local_paths_inside_target_are_omitted_from_actual_request(self):
        self.plan["provenance"]["target"] += "；素材 /Users/private/video.mp4"
        self.plan["provenance"]["source_to_output"][0]["path"] = "/tmp/private.mp4"
        atomic(self.plan_path, self.plan)
        for mode in ("sound", "performance"):
            prompt, result = self.capture(mode)
            self.assertNotIn("/Users/private", prompt)
            self.assertNotIn("/tmp/private", prompt)
            self.assertIn("本地路径已省略", prompt)
            self.assertNotIn("path", result["target_context"]["fields"]["source_to_output"][0])
