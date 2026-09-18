"""真实登记计划/裂变 + 本地持久记录夹具；不调用媒体工具或生成账号。"""

from pathlib import Path
import unittest
from unittest.mock import patch

import test_assembly_variation
from test_bootstrap import bootstrap
from video_remix.assembly import contract_hash
from video_remix.compatibility import inspect
from video_remix.storage import atomic, digest, locked, read


class UpgradeArtifactsTests(unittest.TestCase):
    def setUp(self):
        fixture = test_assembly_variation.AssemblyVariationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.parent["audio"] = {"mode": "silent"}
        atomic(fixture.parent_file, fixture.parent)
        fixture.invoke(True)
        self.root = fixture.root
        self.pin = {"format": "video-remix-runtime.v1", "repo": bootstrap.DEFAULT_REPO, "revision": "a" * 40}
        atomic(self.root / "runtime-lock.json", self.pin)
        contract = read(self.root / "assembly-variations/child-cut/assembly.json")
        atomic(self.root / "assemblies/child-cut/contract.json", contract)
        atomic(self.root / "assemblies/child-cut/run.json", {
            "format": "video-remix-assembly-run.v2", "id": "child-cut", "status": "completed",
            "contract_sha256": contract_hash(contract)})
        names = {clip["variant"] for clip in contract["clips"]}
        frozen = {"job": {"format": "video-remix-production.v1", "id": "film", "budget": 100,
                          "estimate_per_job": 24, "assembly": contract},
                  "spec_sha256": {name: digest(self.root / f"variants/{name}/spec.json") for name in names}}
        atomic(self.root / "productions/film/job.json", frozen)
        atomic(self.root / "productions/film/assembly.json", contract)
        atomic(self.root / "productions/film/run.json", {
            "format": "video-remix-production-run.v1", "id": "film", "status": "completed",
            "job_sha256": contract_hash(frozen), "variants": sorted(names)})

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file() and path.suffix in (".json", ".md")}

    def upgrade(self, apply=True):
        target = {"engine": str(Path(__file__).resolve().parents[1]), "revision": "b" * 40}
        with patch.object(bootstrap, "resolve", return_value=target):
            return bootstrap.upgrade_project(self.root, self.root / "home", bootstrap.DEFAULT_REPO,
                                             apply=apply, offline=True)

    def mutate(self, relative, **changes):
        path = self.root / relative
        atomic(path, {**read(path), **changes})

    def test_registered_artifacts_are_read_only_and_unknown_formats_refused(self):
        original = self.snapshot()
        self.assertTrue(inspect(self.root)["compatible"])
        self.assertEqual(original, self.snapshot())
        files = ("plans/new-a/plan.json", "plans/new-a/variation.json", "plans/new-a/diff.json",
                 "assemblies/child-cut/contract.json", "assemblies/child-cut/run.json",
                 "productions/film/run.json", "assembly-variations/child-cut/control.json",
                 "assembly-variations/child-cut/run.json", "assembly-variations/child-cut/assembly.json")
        for relative in files:
            with self.subTest(relative=relative):
                path = self.root / relative
                before = read(path)
                self.mutate(relative, format="future.v999")
                changed = self.snapshot()
                with self.assertRaises(RuntimeError):
                    self.upgrade()
                self.assertEqual(changed, self.snapshot())
                self.assertEqual(read(self.root / "runtime-lock.json"), self.pin)
                atomic(path, before)
        path = self.root / "productions/film/job.json"
        value = read(path)
        value["job"]["format"] = "future.v999"
        atomic(path, value)
        with self.assertRaises(RuntimeError):
            self.upgrade()
        self.assertEqual(read(self.root / "runtime-lock.json"), self.pin)

    def test_backup_covers_metadata_and_analysis_text_without_media(self):
        directory = self.root / "analysis/clip"
        atomic(directory / "run.json", {"status": "completed"})
        (directory / "analysis.md").write_text("local analysis", encoding="utf-8")
        (directory / "corrections.md").write_text("user correction", encoding="utf-8")
        (self.root / "assemblies/child-cut/output.mp4").write_bytes(b"not-a-video-test-fixture")
        before = self.snapshot()
        result = self.upgrade()
        self.assertTrue(result["applied"])
        backup = Path(result["backup"])
        for relative, value in before.items():
            self.assertEqual((backup / relative).read_bytes(), value, relative)
        self.assertFalse(list(backup.rglob("*.mp4")))
        self.assertFalse((backup / "assets").exists())

    def test_unknown_or_unfinished_orchestration_blocks_upgrade(self):
        cases = {"productions/film/run.json": ("prepared", "generating", "pending", "budget", "assembling", "interrupted", "unknown"),
                 "assemblies/child-cut/run.json": ("running", "unknown"),
                 "assembly-variations/child-cut/run.json": ("preparing", "unknown")}
        for relative, statuses in cases.items():
            before = read(self.root / relative)
            for status in (*statuses, None):
                with self.subTest(relative=relative, status=status):
                    self.mutate(relative, status=status)
                    with self.assertRaisesRegex(RuntimeError, "未完成"):
                        self.upgrade()
                    self.assertEqual(read(self.root / "runtime-lock.json"), self.pin)
            atomic(self.root / relative, before)

    def test_direct_orchestration_locks_block_upgrade(self):
        for name in (".production.lock", ".assembly-variation.lock"):
            with self.subTest(lock=name), locked(self.root / name):
                with self.assertRaisesRegex(RuntimeError, "另一个进程"):
                    self.upgrade()
                self.assertEqual(read(self.root / "runtime-lock.json"), self.pin)

    def test_missing_registered_data_does_not_switch_pin(self):
        for relative in ("plans/new-a/spec.json", "assembly-variations/child-cut/assembly.json",
                         "productions/film/job.json", "assemblies/child-cut/contract.json",
                         "variants/new-a/spec.json"):
            path = self.root / relative
            data = read(path)
            path.unlink()
            with self.subTest(relative=relative), self.assertRaises(RuntimeError):
                self.upgrade()
            self.assertEqual(read(self.root / "runtime-lock.json"), self.pin)
            atomic(path, data)

    def test_changed_registered_association_is_refused(self):
        self.mutate("plans/new-a/spec.json", prompt="changed")
        with self.assertRaises(ValueError):
            inspect(self.root)

    def test_terminal_failures_and_untagged_historical_variation_remain_compatible(self):
        for relative in ("productions/film/run.json", "assemblies/child-cut/run.json",
                         "assembly-variations/child-cut/run.json"):
            self.mutate(relative, status="failed")
        self.assertNotIn("format", read(self.root / "assembly-variations/child-cut/run.json"))
        self.assertTrue(self.upgrade()["applied"])

    def test_changed_frozen_parent_spec_is_refused(self):
        path = self.root / "assembly-variations/child-cut/run.json"
        state = read(path)
        state["parent_specs"]["shot-a"]["sha256"] = "changed"
        atomic(path, state)
        with self.assertRaisesRegex(ValueError, "父规格已变化"):
            inspect(self.root)


class PreparedVariationCompatibilityTests(unittest.TestCase):
    def fixture(self, audio=None):
        fixture = test_assembly_variation.AssemblyVariationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if audio is not None:
            fixture.parent["audio"] = audio
            atomic(fixture.parent_file, fixture.parent)
        result = fixture.invoke(True)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["parent_contract"]["audio"], result["contract"]["audio"])
        return fixture

    def check_compatible(self, fixture):
        before = fixture.snapshot()
        self.assertTrue(inspect(fixture.root)["compatible"])
        self.assertEqual(before, fixture.snapshot())

    def test_original_continuous_asset_fixture_remains_compatible_before_audio_registration(self):
        fixture = self.fixture()
        self.assertEqual(fixture.parent["audio"]["asset"], "voice")
        self.assertNotIn("voice", read(fixture.root / "project.json")["assets"])
        self.check_compatible(fixture)

    def test_continuous_variant_can_be_registered_after_parent_and_child_contracts(self):
        fixture = self.fixture({"mode": "continuous", "variant": "future-voice"})
        self.assertFalse((fixture.root / "variants/future-voice").exists())
        self.check_compatible(fixture)

    def test_continuous_registered_variant_does_not_need_generated_audio_yet(self):
        fixture = self.fixture({"mode": "continuous", "variant": "shot-a"})
        self.assertFalse((fixture.root / "variants/shot-a/run.json").exists())
        self.check_compatible(fixture)

    def test_deferred_audio_does_not_allow_missing_registered_visual_variants(self):
        for variant in ("shot-a", "new-a"):
            fixture = self.fixture()
            (fixture.root / f"variants/{variant}/spec.json").unlink()
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                inspect(fixture.root)


if __name__ == "__main__":
    unittest.main()
