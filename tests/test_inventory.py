import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_remix.cli import main
from video_remix.inventory import inventory
from video_remix.storage import atomic


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "first"
        atomic(self.project / "project.json", {"format": "video-remix-project.v1", "name": "测试"})
        atomic(self.project / "runtime-lock.json", {"revision": "old-locked-revision"})

    def variant(self, name, run=None, raw=False, project=None):
        directory = (project or self.project) / "variants" / name
        atomic(directory / "spec.json", {"id": name, "kind": "replica", "model": "test-model",
               "duration": 15, "inputs": [{"type": "image", "asset": "photo"}], "prompt": "SECRET_PROMPT"})
        if run is not None:
            atomic(directory / "run.json", run)
        if raw:
            (directory / "raw.mp4").write_bytes(b"not-validated-video")
        return directory

    def test_complete_inventory_keeps_planned_pending_failed_and_missing_files(self):
        self.variant("planned")
        self.variant("pending", {"phase": "polling", "task_id": "p"})
        self.variant("failed", {"phase": "failed", "task_id": "f", "credits": 90})
        self.variant("missing", {"phase": "downloaded", "output": "variants/missing/raw.mp4"})
        self.variant("present", {"phase": "downloaded", "output": "variants/present/raw.mp4",
                     "task_id": "done", "engine_revision": "old-run", "credits": 90,
                     "generation_inputs": [{"type": "video"}]}, raw=True)
        result = inventory(self.root)
        self.assertEqual(result["summary"]["phases"], {"failed": 1, "downloaded": 2, "planned": 1, "polling": 1})
        self.assertEqual(result["summary"]["downloaded_files_present"], 1)
        rows = {row["variant"]: row for row in result["variants"]}
        self.assertIn("downloaded_without_output_file", rows["missing"]["issues"])
        self.assertEqual(rows["present"]["input_types"], ["video"])
        self.assertEqual(rows["present"]["project_revision"], "old-locked-revision")
        self.assertEqual(rows["present"]["engine_revision"], "old-run")
        self.assertNotIn("SECRET_PROMPT", json.dumps(result))
        self.assertNotIn("selected", json.dumps(result))

    def test_raw_without_run_and_downloaded_without_output_record_are_not_lost(self):
        self.variant("orphan", raw=True)
        self.variant("no-output", {"phase": "downloaded"}, raw=True)
        no_spec = self.project / "variants" / "no-spec"
        no_spec.mkdir()
        (no_spec / "raw.mp4").write_bytes(b"video")
        rows = {row["variant"]: row for row in inventory(self.project)["variants"]}
        self.assertIn("raw_without_run", rows["orphan"]["issues"])
        self.assertIn("missing:spec.json", rows["no-spec"]["issues"])
        self.assertEqual(rows["no-spec"]["phase"], "unknown")
        self.assertTrue(rows["no-output"]["raw_exists"])
        self.assertFalse(rows["no-output"]["downloaded_file_present"])

    def test_duplicate_tasks_are_flagged_across_projects_not_counted_twice(self):
        other = self.root / "second"
        atomic(other / "project.json", {"format": "video-remix-project.v1"})
        for project in (self.project, other):
            self.variant("a", {"phase": "polling", "task_id": "same-id", "credits": 90}, project=project)
        result = inventory(self.root)
        self.assertEqual(result["summary"]["unique_tasks"], 1)
        self.assertEqual(len(result["duplicate_tasks"]), 1)
        self.assertTrue(all("duplicate_task_id" in row["issues"] for row in result["variants"]))

    def test_bad_json_is_reported_without_aborting_or_leaking_contents(self):
        broken = self.variant("broken", raw=True)
        (broken / "run.json").write_text("SECRET_CREDENTIAL malformed")
        (self.project / "project.json").write_text("[]")
        self.variant("good", {"phase": "failed", "task_id": "good"})
        result = inventory(self.root)
        self.assertEqual(result["summary"]["variants"], 2)
        self.assertIn("unreadable:project.json", result["projects"][0]["issues"])
        self.assertIn("unreadable:run.json", result["variants"][0]["issues"])
        self.assertNotIn("SECRET_CREDENTIAL", json.dumps(result))

    def test_only_one_level_and_no_output_escape(self):
        deep = self.root / "container" / "deep"
        atomic(deep / "project.json", {"format": "video-remix-project.v1"})
        external = self.root / "outside.mp4"
        external.write_bytes(b"outside")
        directory = self.variant("escape", {"phase": "downloaded", "output": str(external)})
        (directory / "raw.mp4").symlink_to(external)
        (self.root / "alias").symlink_to(self.project, target_is_directory=True)
        result = inventory(self.root)
        self.assertEqual(result["summary"]["projects"], 1)
        self.assertFalse(result["variants"][0]["output_exists"])
        self.assertFalse(result["variants"][0]["raw_exists"])
        self.assertIn("output_outside_project_or_symlink", result["variants"][0]["issues"])

    def test_cli_bypasses_runtime_pin_and_does_not_write_or_query(self):
        self.variant("planned")
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = io.StringIO()
        with patch("video_remix.cli.project_at", side_effect=AssertionError("must not find active project")), \
                patch("video_remix.cli.Dreamina", side_effect=AssertionError("must not query")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(["inventory", "--root", str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue())["summary"]["variants"], 1)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_empty_collection(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(inventory(empty)["summary"]["variants"], 0)

    def test_terminal_provider_failure_flags_stale_phase_without_rewriting(self):
        stale = self.variant("stale", {"phase": "polling", "provider_status": "fail"})
        self.variant("normal", {"phase": "polling", "provider_status": "generating"})
        self.variant("unknown-provider", {"phase": "polling", "provider_status": "stopped"})
        self.variant("terminal", {"phase": "failed", "provider_status": "fail"})
        before = (stale / "run.json").read_bytes()
        rows = {row["variant"]: row for row in inventory(self.project)["variants"]}
        issue = "terminal_provider_failure_with_nonterminal_phase"
        self.assertIn(issue, rows["stale"]["issues"])
        self.assertEqual(rows["stale"]["phase"], "polling")
        self.assertEqual(rows["stale"]["provider_status"], "fail")
        for name in ("normal", "unknown-provider", "terminal"):
            self.assertNotIn(issue, rows[name]["issues"])
        self.assertEqual((stale / "run.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
