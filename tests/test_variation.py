import copy
from pathlib import Path
import tempfile
import unittest

from video_remix.cli import dispatch, parser
from video_remix.creative import compile_plan
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, read
from video_remix.variation import vary


class VariationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        initialize(self.root, "controlled variation")
        image = Path(self.temp.name) / "source.png"
        image.write_bytes(b"test-image")
        for asset in ("product", "anchor", "terrace-anchor"):
            add_asset(self.root, asset, image, asset)
        self.base = {"format": "video-remix-plan.v1", "id": "base", "kind": "replica",
                     "provider": "dreamina", "model": "seedance2.0fast_vip",
                     "mode": "image_text", "duration": 15, "ratio": "9:16", "resolution": "720p",
                     "inputs": [{"type": "image", "asset": "anchor", "role": "scene and person"},
                                {"type": "image", "asset": "product", "role": "product source"}],
                     "intent": "Discovery, detail, on-feet reveal, walking. Keep causal motion.",
                     "product": "Pink shoe with cream sole.",
                     "look": "A woman in black leggings on dark asphalt. Downward phone framing.",
                     "sound": "Fast connected enthusiastic speech. Never pause at each cut.",
                     "shots": [{"start": 0, "end": 5, "action": "Hand tips shoe over dark asphalt.",
                                "camera": "Downward view.", "sync": "Reveal on the first emphasis."},
                               {"start": 5, "end": 15, "action": "Cut to already worn shoes, then walk.",
                                "delivery": "Voice continues across the cut."}],
                     "provenance": {"source_to_output": [{"source": [0, 15], "output": [0, 15]}]}}
        self.base_file = Path(self.temp.name) / "base.json"
        atomic(self.base_file, self.base)
        compile_plan(self.root, self.base_file)
        self.control = {"format": "video-remix-variation.v1", "id": "terrace", "parent": "base",
                        "frozen_core": {"mechanism": "Connected enthusiastic discovery and reveal.",
                                        "product_assets": ["product"]},
                        "slots": {"scene": {"text": [
                            {"path": "look", "from": "dark asphalt", "to": "wooden terrace"},
                            {"path": "shots.0.action", "from": "dark asphalt", "to": "wooden terrace"}],
                            "images": [{"from": "anchor", "to": "terrace-anchor"}]}}}
        self.control_file = Path(self.temp.name) / "controls.json"

    def run_control(self, control=None):
        atomic(self.control_file, self.control if control is None else control)
        return vary(self.root, self.control_file)

    def assert_no_child(self):
        self.assertFalse((self.root / "plans/terrace").exists())
        self.assertFalse((self.root / "variants/terrace").exists())

    def test_scene_change_freezes_core_timing_mode_and_product(self):
        before = (self.root / "plans/base/plan.json").read_bytes()
        result = self.run_control()
        child = read(result["plan"])
        self.assertEqual(child["look"], self.base["look"].replace("dark asphalt", "wooden terrace"))
        for field in ("intent", "product", "sound", "duration", "mode", "model", "provider", "provenance"):
            self.assertEqual(child[field], self.base[field])
        for old, new in zip(self.base["shots"], child["shots"]):
            for field in ("start", "end", "sync", "delivery"):
                self.assertEqual(old.get(field), new.get(field))
        self.assertEqual(child["inputs"][1], self.base["inputs"][1])
        self.assertEqual(child["inputs"][0], {**self.base["inputs"][0], "asset": "terrace-anchor"})
        self.assertEqual((child["kind"], child["parent"]), ("variation", "base"))
        self.assertEqual(read(result["control"]), self.control)
        changes = {item["path"] for item in read(result["diff"])["changes"]}
        self.assertEqual(changes, {"id", "kind", "parent", "change", "look", "shots.0.action", "inputs.0.asset"})
        self.assertEqual((self.root / "plans/base/plan.json").read_bytes(), before)
        self.assertFalse((self.root / "variants/terrace/run.json").exists())

    def test_person_and_outfit_are_explicit_simultaneous_replacements(self):
        self.control["slots"] = {
            "person": {"text": [{"path": "look", "from": "A woman", "to": "A man"}]},
            "outfit": {"text": [{"path": "look", "from": "black leggings", "to": "blue jeans"}]}}
        child = read(self.run_control()["plan"])
        self.assertIn("A man in blue jeans", child["look"])
        self.assertEqual(child["sound"], self.base["sound"])
        self.assertEqual(child["inputs"], self.base["inputs"])

    def test_silent_before_after_plan_keeps_sound_and_reveal(self):
        self.base.update(id="silent", sound="No speech. Shoe contact and one soft footfall.",
                         intent="Before discomfort → product contact → after change at 8 seconds.")
        atomic(self.base_file, self.base)
        compile_plan(self.root, self.base_file)
        self.control["parent"] = "silent"
        child = read(self.run_control()["plan"])
        self.assertEqual(child["sound"], self.base["sound"])
        self.assertEqual(child["intent"], self.base["intent"])
        self.assertEqual(child["shots"][1], self.base["shots"][1])

    def test_frozen_fields_cannot_be_replaced(self):
        for path in ("sound", "intent", "product", "duration", "mode", "inputs.0.type",
                     "shots.0.start", "shots.0.sync", "shots.1.delivery", "provenance.source_to_output"):
            with self.subTest(path=path):
                self.control["slots"] = {"scene": {"text": [{"path": path, "from": "old", "to": "new"}]}}
                with self.assertRaisesRegex(ValueError, "只允许替换"):
                    self.run_control()
                self.assert_no_child()

    def test_unknown_slots_or_top_level_overrides_rejected(self):
        for key in ("sound", "mode", "duration", "inputs"):
            control = copy.deepcopy(self.control)
            control[key] = "override"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_control(control)
        self.control["slots"]["voice"] = {"text": []}
        with self.assertRaisesRegex(ValueError, "slots"):
            self.run_control()
        self.assert_no_child()

    def test_missing_ambiguous_and_overlapping_source_phrases_rejected(self):
        for replacements in (
                [{"path": "look", "from": "not present", "to": "terrace"}],
                [{"path": "look", "from": "a", "to": "terrace"}],
                [{"path": "look", "from": "dark asphalt", "to": "terrace"},
                 {"path": "look", "from": "asphalt", "to": "wood"}]):
            self.control["slots"] = {"scene": {"text": replacements}}
            with self.assertRaises(ValueError):
                self.run_control()
            self.assert_no_child()

    def test_product_and_non_image_inputs_cannot_be_swapped(self):
        for asset in ("product", "not-an-input"):
            self.control["slots"] = {"scene": {"images": [{"from": asset, "to": "terrace-anchor"}]}}
            with self.subTest(asset=asset), self.assertRaises(ValueError):
                self.run_control()
        self.assert_no_child()

    def test_video_and_audio_conditions_remain_frozen(self):
        media = Path(self.temp.name) / "reference.mp4"
        media.write_bytes(b"test-reference")
        add_asset(self.root, "performance", media, "performance")
        for kind, mode in (("video", "video_reference"), ("audio", "image_audio")):
            base = {**self.base, "id": "base-" + kind, "mode": mode,
                    "inputs": [*self.base["inputs"], {"type": kind, "asset": "performance", "role": "rhythm"}]}
            atomic(self.base_file, base)
            compile_plan(self.root, self.base_file)
            control = {**self.control, "id": "child-" + kind, "parent": base["id"]}
            child = read(self.run_control(control)["plan"])
            self.assertEqual(child["mode"], mode)
            self.assertEqual(child["inputs"][-1], base["inputs"][-1])
            control = {**control, "id": "invalid-" + kind,
                       "slots": {"scene": {"images": [{"from": "performance", "to": "terrace-anchor"}]}}}
            with self.assertRaisesRegex(ValueError, "不能替换音视频"):
                self.run_control(control)

    def test_missing_replacement_asset_creates_no_partial_plan(self):
        self.control["slots"]["scene"]["images"][0]["to"] = "missing-asset"
        with self.assertRaises(ValueError):
            self.run_control()
        self.assert_no_child()

    def test_parent_plan_must_match_frozen_spec(self):
        source = self.root / "plans/base/plan.json"
        base = read(source)
        base["sound"] = "Accidentally changed after generation."
        atomic(source, base)
        with self.assertRaisesRegex(ValueError, "父计划与冻结规格不一致"):
            self.run_control()
        self.assert_no_child()

    def test_existing_child_is_not_overwritten(self):
        self.run_control()
        before = (self.root / "plans/terrace/plan.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.run_control()
        self.assertEqual((self.root / "plans/terrace/plan.json").read_bytes(), before)

    def test_cli_routes_control_file_without_generation(self):
        atomic(self.control_file, self.control)
        args = parser().parse_args(["--project", str(self.root), "vary", str(self.control_file)])
        result = dispatch(args)
        self.assertEqual(result["variant"], "terrace")
        self.assertTrue(Path(result["diff"]).is_file())
        self.assertFalse((self.root / "variants/terrace/run.json").exists())


if __name__ == "__main__":
    unittest.main()
