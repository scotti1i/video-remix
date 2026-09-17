import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_remix.assembly_variation import vary_assembly
from video_remix.cli import dispatch, parser
from video_remix.creative import store_plan
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, digest, read
from video_remix.variation import vary


class AssemblyVariationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "project"
        initialize(self.root, "shot variation")
        image = self.directory / "source.png"
        image.write_bytes(b"unit-test-input")
        for name in ("product", "scene", "new-scene"):
            add_asset(self.root, name, image, name)
        plan = {"format": "video-remix-plan.v1", "kind": "replica", "provider": "dreamina",
                "model": "seedance2.0fast_vip", "mode": "image_text", "duration": 4,
                "ratio": "9:16", "resolution": "720p", "intent": "Keep demonstration and repeat.",
                "product": "Black goggles", "look": "Workshop with a blue door.",
                "sound": "Continuous serious explanation, not cheerful UGC.",
                "inputs": [{"type": "image", "asset": "product", "role": "product truth"},
                           {"type": "image", "asset": "scene", "role": "this shot's visual"}],
                "shots": [{"start": 0, "end": 4, "action": "Darken, recover, darken again.",
                           "sync": "Show the effect during the explanation."}]}
        for name in ("shot-a", "shot-b"):
            store_plan(self.root, {**plan, "id": name})
        self.parent = {"format": "video-remix-assembly.v2", "id": "parent-cut",
                       "clips": [{"variant": "shot-a", "in": 0.2, "out": 3.2},
                                 {"variant": "shot-b", "in": 0, "out": 2.4},
                                 {"variant": "shot-a", "in": 0.4, "out": 1.8}],
                       "audio": {"mode": "continuous", "asset": "voice", "in": 0.3,
                                 "provenance": "User approved original narration; not new speech."}}
        self.parent_file = self.directory / "parent.json"
        atomic(self.parent_file, self.parent)
        self.control = {"format": "video-remix-assembly-variation.v1", "id": "child-cut",
                        "parent": "parent.json", "changes": [{"clip": 3, "control": self.shot_control()}]}
        self.file = self.directory / "control.json"

    def shot_control(self, child="new-a", parent="shot-a"):
        return {"format": "video-remix-variation.v1", "id": child, "parent": parent,
                "frozen_core": {"mechanism": "Darken/recover repeatedly", "product_assets": ["product"]},
                "slots": {"scene": {"text": [{"path": "look", "from": "blue door", "to": "green door"}],
                                    "images": [{"from": "scene", "to": "new-scene",
                                                "role": "same close POV in the new workshop"}]}}}

    def invoke(self, execute=False):
        atomic(self.file, self.control)
        return vary_assembly(self.root, self.file, execute)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_preview_is_read_only_and_repeated_parent_is_indexed_unambiguously(self):
        before = self.snapshot()
        result = self.invoke()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(result["contract"]["clips"][0], self.parent["clips"][0])
        self.assertEqual(result["contract"]["clips"][2], {**self.parent["clips"][2], "variant": "new-a"})
        self.assertEqual(result["contract"]["audio"], self.parent["audio"])
        self.assertFalse(result["execute"])

    def test_execute_registers_only_changed_shot_without_generation_or_render(self):
        original = self.snapshot()
        result = self.invoke(True)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["registered_variants"], ["new-a"])
        contract = read(result["assembly"])
        self.assertEqual(contract["audio"], self.parent["audio"])
        for before, after in zip(self.parent["clips"], contract["clips"]):
            self.assertEqual((before["in"], before["out"]), (after["in"], after["out"]))
        child = read(self.root / "plans/new-a/plan.json")
        base = read(self.root / "plans/shot-a/plan.json")
        for field in ("sound", "product", "shots", "intent", "duration"):
            self.assertEqual(child[field], base[field])
        self.assertIn("green door", child["look"])
        self.assertFalse((self.root / "variants/new-a/run.json").exists())
        self.assertFalse((self.root / "assemblies").exists())
        for path, contents in original.items():
            self.assertEqual((self.root / path).read_bytes(), contents)

    def test_film_variation_accepts_state_only_shot_with_no_protected_product_image(self):
        for asset in ("clear", "dark", "new-clear", "new-dark"):
            add_asset(self.root, asset, self.directory / "source.png", asset)
        base = read(self.root / "plans/shot-a/plan.json")
        base["id"] = "pov"
        base["inputs"] = [{"type": "image", "asset": "clear", "role": "close clear state"},
                          {"type": "image", "asset": "dark", "role": "same composition dark state"}]
        store_plan(self.root, base)
        self.parent["clips"][2]["variant"] = "pov"
        atomic(self.parent_file, self.parent)
        control = self.shot_control("new-pov", "pov")
        control["frozen_core"]["product_assets"] = []
        control["slots"]["scene"]["images"] = [
            {"from": "clear", "to": "new-clear"}, {"from": "dark", "to": "new-dark"}]
        self.control["changes"] = [{"clip": 3, "control": control}]
        before = self.snapshot()
        preview = self.invoke()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(preview["contract"]["audio"], self.parent["audio"])
        result = self.invoke(True)
        child = read(self.root / "plans/new-pov/plan.json")
        self.assertEqual(child["product"], base["product"])
        self.assertEqual(child["sound"], base["sound"])
        self.assertEqual(child["shots"], base["shots"])
        self.assertEqual([entry["asset"] for entry in child["inputs"]], ["new-clear", "new-dark"])
        self.assertEqual(result["contract"]["clips"][2], {**self.parent["clips"][2], "variant": "new-pov"})

    def test_all_changes_validate_before_any_child_is_written(self):
        bad = self.shot_control("new-b", "shot-b")
        bad["slots"]["scene"]["images"][0]["to"] = "missing"
        self.control["changes"].append({"clip": 2, "control": bad})
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.invoke(True)
        after = self.snapshot()
        after.pop(".assembly-variation.lock", None)
        self.assertEqual(after, before)

    def test_duplicate_or_wrong_clip_and_mismatched_parent_rejected(self):
        original = copy.deepcopy(self.control)
        for number in (0, 4, True, "1", -1):
            self.control = copy.deepcopy(original)
            self.control["changes"][0]["clip"] = number
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.invoke()
        self.control = copy.deepcopy(original)
        self.control["changes"].append(copy.deepcopy(self.control["changes"][0]))
        with self.assertRaisesRegex(ValueError, "不重复"):
            self.invoke()
        self.control = copy.deepcopy(original)
        self.control["changes"][0]["control"]["parent"] = "shot-b"
        with self.assertRaisesRegex(ValueError, "parent"):
            self.invoke()

    def test_parent_or_output_id_collision_and_repeat_do_not_overwrite(self):
        self.control["id"] = "parent-cut"
        with self.assertRaisesRegex(ValueError, "新组装 ID"):
            self.invoke(True)
        self.control["id"] = "child-cut"
        self.invoke(True)
        before = self.snapshot()
        for execute in (False, True):
            with self.assertRaisesRegex(ValueError, "已存在"):
                self.invoke(execute)
            self.assertEqual(before, self.snapshot())

    def test_existing_partial_repair_can_be_explicitly_reused(self):
        control_file = self.directory / "single.json"
        atomic(control_file, self.shot_control())
        vary(self.root, control_file)
        self.control["changes"] = [{"clip": 1, "variant": "new-a"}]
        result = self.invoke(True)
        self.assertEqual(result["registered_variants"], [])
        self.assertEqual(result["contract"]["clips"][0]["variant"], "new-a")
        self.assertEqual(result["contract"]["clips"][2]["variant"], "shot-a")

    def test_existing_repair_must_match_identity_parent_and_parameters(self):
        control_file = self.directory / "single.json"
        atomic(control_file, self.shot_control())
        vary(self.root, control_file)
        self.control["changes"] = [{"clip": 1, "variant": "new-a"}]
        file = self.root / "variants/new-a/spec.json"
        original = read(file)
        for field, value in (("id", "other"), ("parent", "shot-b"), ("duration", 5),
                             ("ratio", "16:9"), ("creative_mode", "video_reference")):
            atomic(file, {**original, field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.invoke()
        atomic(file, original)
        atomic(self.root / "variants/new-a/run.json", {"spec_sha256": "wrong"})
        with self.assertRaisesRegex(ValueError, "运行后规格被修改"):
            self.invoke()

    def test_duplicate_child_id_and_invalid_union_leave_no_registration(self):
        change = copy.deepcopy(self.control["changes"][0])
        change["clip"] = 1
        self.control["changes"].append(change)
        with self.assertRaisesRegex(ValueError, "ID 不能重复"):
            self.invoke()
        self.control["changes"] = [{"clip": 1, "variant": "shot-b", "control": self.shot_control()}]
        with self.assertRaisesRegex(ValueError, "恰选"):
            self.invoke()
        self.assertFalse((self.root / "variants/new-a").exists())

    def test_parent_change_during_registration_does_not_publish_contract(self):
        def changing_store(root, plan, artifacts):
            result = store_plan(root, plan, artifacts)
            atomic(self.parent_file, {**self.parent, "audio": {"mode": "silent"}})
            return result

        with patch("video_remix.assembly_variation.store_plan", side_effect=changing_store):
            with self.assertRaisesRegex(ValueError, "父合同变化"):
                self.invoke(True)
        self.assertFalse((self.root / "assembly-variations/child-cut/assembly.json").exists())
        run = read(self.root / "assembly-variations/child-cut/run.json")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["registered_variants"], ["new-a"])

    def test_interrupted_registration_keeps_receipt_and_originals(self):
        self.control["changes"].append({"clip": 2, "control": self.shot_control("new-b", "shot-b")})
        original = self.snapshot()
        calls = []

        def failing_store(root, plan, artifacts):
            calls.append(plan["id"])
            if len(calls) == 2:
                raise OSError("simulated write failure")
            return store_plan(root, plan, artifacts)

        with patch("video_remix.assembly_variation.store_plan", side_effect=failing_store):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.invoke(True)
        run = read(self.root / "assembly-variations/child-cut/run.json")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["registered_variants"], ["new-a"])
        self.assertIn("新批次 ID", run["recovery"])
        self.assertFalse((self.root / "assembly-variations/child-cut/assembly.json").exists())
        for path, contents in original.items():
            self.assertEqual((self.root / path).read_bytes(), contents)
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.invoke(True)
        self.control["id"] = "recovered-cut"
        self.control["changes"][0] = {"clip": 3, "variant": "new-a"}
        self.assertEqual(self.invoke(True)["status"], "prepared")

    def test_audio_and_contract_fields_cannot_be_overridden(self):
        for field in ("audio", "clips", "duration"):
            self.control[field] = {}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.invoke()
            del self.control[field]
        for audio in ({"mode": "silent"}, {"mode": "clips"},
                      {"mode": "continuous", "variant": "shot-a", "in": 0.1}):
            self.parent["audio"] = audio
            atomic(self.parent_file, self.parent)
            self.assertEqual(self.invoke()["contract"]["audio"], audio)

    def test_changed_parent_spec_and_frozen_assembly_are_rejected(self):
        file = self.root / "variants/shot-a/spec.json"
        atomic(self.root / "variants/shot-a/run.json", {"spec_sha256": digest(file)})
        spec = read(file)
        spec["prompt"] += " changed"
        atomic(file, spec)
        with self.assertRaisesRegex(ValueError, "规格被修改"):
            self.invoke()
        (self.root / "variants/shot-a/run.json").unlink()
        frozen = copy.deepcopy(self.parent)
        frozen["audio"] = {"mode": "silent"}
        atomic(self.root / "assemblies/parent-cut/contract.json", frozen)
        with self.assertRaisesRegex(ValueError, "冻结合同不同"):
            self.invoke()

    def test_cli_defaults_to_preview(self):
        atomic(self.file, self.control)
        args = parser().parse_args(["--project", str(self.root), "vary-assembly", str(self.file)])
        before = self.snapshot()
        self.assertFalse(dispatch(args)["execute"])
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
