import copy
from pathlib import Path
import tempfile
import unittest

from video_remix.creative import assemble, compile_plan
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, read


def plan():
    return {"format": "video-remix-plan.v1", "id": "sample", "mode": "image_text",
            "kind": "replica", "provider": "dreamina", "model": "seedance2.0fast_vip",
            "duration": 15, "ratio": "9:16", "resolution": "720p",
            "inputs": [{"type": "image", "asset": "product", "role": "exact packaging"}],
            "intent": "retain the reveal", "product": "cream sidewall", "look": "uneven window light",
            "sound": "No speech. Two quiet footfalls.",
            "shots": [{"start": 0, "end": 15, "action": "touch then release",
                       "sync": "second footfall occurs AFTER the turn", "delivery": "no speech"}]}


class CreativeTests(unittest.TestCase):
    def test_compiled_plan_is_frozen_registered_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            initialize(root, "test")
            image = Path(directory) / "input.png"
            image.write_bytes(b"test-image")
            add_asset(root, "product", image, "product")
            source = plan()
            source["provenance"] = {"uncertain": ["unmeasured light direction"]}
            file = Path(directory) / "plan.json"
            atomic(file, source)
            result = compile_plan(root, file)
            self.assertEqual(read(Path(result["plan"])), source)
            spec = read(Path(result["spec"]))
            self.assertEqual(spec["prompt"], assemble(source)["prompt"])
            self.assertNotIn("unmeasured light direction", spec["prompt"])
            self.assertFalse((root / "variants/sample/run.json").exists())
            with self.assertRaises(ValueError):
                compile_plan(root, file)

    def test_exact_content_and_input_order_survive_compilation(self):
        source = plan()
        before = copy.deepcopy(source)
        result = assemble(source)
        for field in ("intent", "product", "look", "sound"):
            self.assertIn(source[field], result["prompt"])
        for field in ("action", "sync", "delivery"):
            self.assertIn(source["shots"][0][field], result["prompt"])
        self.assertIn("@image1: exact packaging", result["prompt"])
        self.assertEqual(result["inputs"], source["inputs"])
        self.assertEqual(source, before)

    def test_changed_rerun_leaves_no_partial_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            initialize(root, "test")
            image = Path(directory) / "input.png"
            image.write_bytes(b"test-image")
            add_asset(root, "product", image, "product")
            source = plan()
            file = Path(directory) / "plan.json"
            atomic(file, source)
            compile_plan(root, file)
            source.update(id="changed-rerun", kind="rerun", parent="sample", sound="Different speech.")
            atomic(file, source)
            with self.assertRaisesRegex(ValueError, "rerun"):
                compile_plan(root, file)
            self.assertFalse((root / "plans/changed-rerun").exists())
            self.assertFalse((root / "variants/changed-rerun").exists())

    def test_no_video_experiment_cannot_smuggle_audio_or_video(self):
        for kind in ("audio", "video"):
            source = plan()
            source["inputs"].append({"type": kind, "asset": "ref", "role": "performance"})
            with self.assertRaises(ValueError):
                assemble(source)

    def test_audio_reference_is_a_distinct_mode(self):
        source = plan()
        source["mode"] = "image_audio"
        with self.assertRaises(ValueError):
            assemble(source)
        source["inputs"].append({"type": "audio", "asset": "audio", "role": "prosody"})
        self.assertIn("@audio1: prosody", assemble(source)["prompt"])

    def test_real_shots_not_fixed_count_but_reject_impossible_times(self):
        for start, end in ((0, 16), (4, 3), (float("nan"), 4)):
            source = plan()
            source["shots"][0].update(start=start, end=end)
            with self.assertRaises(ValueError):
                assemble(source)
        self.assertIn("[0–15s]", assemble(plan())["prompt"])
