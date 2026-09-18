import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_remix.assessment_context import frozen_target
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

    def invoke(self, mode, brief="这次只看动作，允许声音变轻快", variant="v1"):
        if mode == "sound":
            return assess(self.root, variant, "ref", mode, brief, "gemini-test")
        return assess_performance(self.root, variant, "ref", brief, "gemini-test")

    def rerun(self, name, parent="v1", **changes):
        spec = read(self.root / f"variants/{parent}/spec.json")
        spec.update(id=name, kind="rerun", parent=parent, change="same conditions", **changes)
        directory = self.root / "variants" / name
        atomic(directory / "spec.json", spec)
        raw = directory / "raw.mp4"
        raw.write_bytes(b"rerun-output")
        atomic(directory / "run.json", {"phase": "downloaded", "output": f"variants/{name}/raw.mp4",
                                       "output_sha256": digest(raw),
                                       "spec_sha256": digest(directory / "spec.json")})
        return directory

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

    def test_same_condition_rerun_chain_inherits_target_in_both_requests(self):
        self.rerun("v2")
        directory = self.rerun("v3", "v2")
        for mode in ("sound", "performance"):
            payload = {"candidates": [{"content": {"parts": [{"text": "辅助意见"}]}}]}
            with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())) as fetch:
                result = self.invoke(mode, variant="v3")
            run = read(result["run"])
            context = run["target_context"]
            self.assertEqual(context["status"], "matched")
            self.assertEqual(context["inherited_from"], "v1")
            self.assertEqual([item["variant_id"] for item in context["ancestry"]], ["v3", "v2", "v1"])
            self.assertEqual(context["spec_sha256"], digest(directory / "spec.json"))
            self.assertEqual(context["plan_sha256"], digest(self.plan_path))
            prompt = json.loads(fetch.call_args.args[0].data)["contents"][0]["parts"][0]["text"]
            self.assertIn(self.plan["provenance"]["target"], prompt)
            self.assertNotIn(str(self.root), prompt)

    def test_every_link_checks_conditions_even_with_matching_submission_hash(self):
        for changed in ({"prompt": "changed"}, {"duration": 14}, {"creative_mode": "changed"},
                        {"inputs": [{"type": "image", "asset": "product", "role": "changed"}]},
                        {"new_provider_option": "changed"}, {"generation_route": "first_frame"}):
            with self.subTest(changed=changed):
                self.rerun("v2", **changed)
                self.rerun("v3", "v2")
                with patch("urllib.request.urlopen") as fetch, self.assertRaisesRegex(ValueError, "生成条件不一致"):
                    self.invoke("performance", variant="v3")
                fetch.assert_not_called()

    def test_explicit_reference_rerun_inherits_legacy_default_reference_target(self):
        self.rerun("v2", generation_route="reference")
        context = frozen_target(self.root, "v2")
        self.assertEqual(context["status"], "matched")
        self.assertEqual(context["inherited_from"], "v1")

    def test_each_ancestor_submission_hash_is_verified(self):
        self.rerun("v2")
        self.rerun("v3", "v2")
        for name in ("v2", "v1"):
            path = self.root / f"variants/{name}/run.json"
            record = read(path)
            atomic(path, {**record, "spec_sha256": "tampered"})
            with self.assertRaisesRegex(ValueError, "规格与提交记录不一致"):
                frozen_target(self.root, "v3")
            atomic(path, record)

    def test_variation_without_own_plan_does_not_inherit_replica_target(self):
        directory = self.rerun("v2")
        spec = read(directory / "spec.json")
        spec["kind"] = "variation"
        atomic(directory / "spec.json", spec)
        run = read(directory / "run.json")
        atomic(directory / "run.json", {**run, "spec_sha256": digest(directory / "spec.json")})
        self.rerun("v3", "v2")
        self.assertEqual(frozen_target(self.root, "v3"), {"status": "legacy", "reason": "no_plan"})

    def test_chain_without_target_is_legacy_not_fabricated(self):
        self.rerun("v2")
        self.plan.pop("provenance")
        atomic(self.plan_path, self.plan)
        self.assertEqual(frozen_target(self.root, "v2")["reason"], "no_target")

    def test_missing_ancestor_hash_is_reported_without_claiming_full_verification(self):
        self.rerun("v2")
        run = read(self.run_path)
        run.pop("spec_sha256")
        atomic(self.run_path, run)
        context = frozen_target(self.root, "v2")
        self.assertTrue(context["submitted_spec_verified"])
        self.assertFalse(context["ancestry"][-1]["submitted_spec_verified"])

    def test_cycle_and_invalid_parent_fail_before_requests(self):
        directory = self.rerun("v2")
        for parent in ("v2", "../outside", "/tmp/outside", None, "missing"):
            spec = read(directory / "spec.json")
            spec["parent"] = parent
            atomic(directory / "spec.json", spec)
            run = read(directory / "run.json")
            atomic(directory / "run.json", {**run, "spec_sha256": digest(directory / "spec.json")})
            with self.subTest(parent=parent), patch("urllib.request.urlopen") as fetch:
                with self.assertRaises(ValueError):
                    self.invoke("performance", variant="v2")
                fetch.assert_not_called()

    def test_escaped_ancestor_plan_is_rejected(self):
        self.rerun("v2")
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "plan.json"
            atomic(path, self.plan)
            self.plan_path.unlink()
            self.plan_path.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "逃逸"):
                frozen_target(self.root, "v2")

    def test_invalid_ancestor_id_and_escaped_submission_are_rejected(self):
        self.rerun("v2")
        original = read(self.spec_path)
        atomic(self.spec_path, {**original, "id": "other"})
        with self.assertRaisesRegex(ValueError, "ID 不匹配"):
            frozen_target(self.root, "v2")
        atomic(self.spec_path, original)
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "run.json"
            atomic(path, read(self.run_path))
            self.run_path.unlink()
            self.run_path.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "逃逸"):
                frozen_target(self.root, "v2")
