import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_remix.project import add_asset, add_variant, compare, initialize, status
from video_remix.runner import attach, execute
from video_remix.storage import atomic, project_at, read


class FakeDreamina:
    def __init__(self, price=42, ambiguous=False, fail_download=False):
        self.price = price
        self.ambiguous = ambiguous
        self.fail_download = fail_download
        self.submissions = []
        self.queries = []

    def account(self):
        return {"credits": 500}

    def preflight(self, items):
        return None

    def arguments(self, spec, root, assets):
        return ["dreamina", "multimodal2video", "--prompt", spec["prompt"]]

    def submit(self, args):
        self.submissions.append(args)
        if self.ambiguous:
            raise TimeoutError("transport lost after submit")
        return {"submit_id": f"task-{len(self.submissions)}", "credit_count": self.price}

    def query(self, task_id):
        self.queries.append(task_id)
        return {"submit_id": task_id, "gen_status": "success", "credit_count": self.price,
                "prompt": 'literal $HOME `echo test`\nKeep this newline.\n'}

    def download(self, task_id, directory):
        if self.fail_download:
            raise TimeoutError("download interrupted")
        target = directory / "provider-output.mp4"
        target.write_bytes(b"test-media-bytes")
        return target


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "user-project"
        initialize(self.root, "真实任务管理测试")
        file = Path(self.temp.name) / "input.jpg"
        file.write_bytes(b"test image")
        add_asset(self.root, "product", file, "包装")
        self.spec = {"id": "replica", "kind": "replica", "provider": "dreamina",
                     "model": "seedance2.0fast_vip", "duration": 7, "ratio": "9:16", "resolution": "720p",
                     "inputs": [{"type": "image", "asset": "product", "role": "包装"}],
                     "prompt": 'literal $HOME `echo test`\nKeep this newline.\n'}
        self.add("replica")
        self.probe = patch("video_remix.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "video\n", ""))
        self.probe.start()
        self.addCleanup(self.probe.stop)
        self.disk = patch("video_remix.runner.disk_check")
        self.disk.start()
        self.addCleanup(self.disk.stop)

    def add(self, name, **changes):
        file = Path(self.temp.name) / (name + ".json")
        atomic(file, {**self.spec, "id": name, **changes})
        return add_variant(self.root, file)

    def run_job(self, ids=None, budget=100, estimate=42, provider=None, real=True):
        return execute(self.root, ids or ["replica"], budget, estimate, execute=real,
                       provider=provider or FakeDreamina())

    def test_preview_never_submits_or_checks_account(self):
        provider = FakeDreamina()
        provider.account = lambda: self.fail("preview checked account")
        self.run_job(provider=provider, real=False)
        self.assertEqual(provider.submissions, [])
        self.assertFalse((self.root / "variants/replica/run.json").exists())

    def test_resume_preserves_prompt_and_never_resubmits(self):
        provider = FakeDreamina()
        self.run_job(provider=provider)
        self.run_job(provider=provider)
        self.assertEqual(len(provider.submissions), 1)
        self.assertEqual(provider.submissions[0][-1], self.spec["prompt"])
        run = read(self.root / "variants/replica/run.json")
        self.assertEqual(run["phase"], "downloaded")
        self.assertFalse(run["postprocessing"])
        self.assertEqual((self.root / run["output"]).read_bytes(), b"test-media-bytes")

    def test_ambiguous_submit_blocks_resume_and_can_attach(self):
        provider = FakeDreamina(ambiguous=True)
        with self.assertRaises(TimeoutError):
            self.run_job(provider=provider)
        with self.assertRaisesRegex(ValueError, "结果不明"):
            self.run_job(provider=provider)
        self.assertEqual(len(provider.submissions), 1)
        attach(self.root, "replica", "task-1", provider)
        self.run_job(provider=provider)
        self.assertEqual(len(provider.submissions), 1)

    def test_download_retry_does_not_submit_again(self):
        provider = FakeDreamina(fail_download=True)
        with self.assertRaises(TimeoutError):
            self.run_job(provider=provider)
        provider.fail_download = False
        self.run_job(provider=provider)
        self.assertEqual(len(provider.submissions), 1)
        self.assertEqual(provider.queries, ["task-1", "task-1"])

    def test_changed_input_stops_before_submit(self):
        asset = read(self.root / "project.json")["assets"]["product"]
        (self.root / asset["path"]).write_bytes(b"changed")
        provider = FakeDreamina()
        with self.assertRaisesRegex(ValueError, "素材已变化"):
            self.run_job(provider=provider)
        self.assertFalse(provider.submissions)

    def test_price_drift_stops_next_variant(self):
        self.add("variation", kind="variation", parent="replica", change="揭晓速度")
        provider = FakeDreamina(price=84)
        result = self.run_job(["replica", "variation"], budget=200, provider=provider)
        self.assertEqual(result["stop"], "price_exceeded_estimate")
        self.assertEqual(len(provider.submissions), 1)

    def test_budget_counts_previous_runs_on_resume(self):
        self.add("variation", kind="variation", parent="replica", change="场景")
        provider = FakeDreamina()
        self.run_job(["replica", "variation"], budget=42, provider=provider)
        self.run_job(["replica", "variation"], budget=42, provider=provider)
        self.assertEqual(len(provider.submissions), 1)

    def test_all_specs_checked_before_first_submit(self):
        provider = FakeDreamina()
        with self.assertRaises(FileNotFoundError):
            self.run_job(["replica", "missing"], provider=provider)
        self.assertFalse(provider.submissions)

    def test_project_movable_and_compare_links_local_video(self):
        self.run_job()
        moved = self.root.parent / "moved"
        self.root.rename(moved)
        self.assertEqual(project_at(moved), moved.resolve())
        self.assertEqual(status(moved)["variants"][0]["phase"], "downloaded")
        report = compare(moved)
        self.assertIn('src="variants/replica/raw.mp4"', Path(report["report"]).read_text())

    def test_no_overwrite_or_invalid_lineage(self):
        with self.assertRaises(ValueError):
            self.add("replica")
        with self.assertRaises(ValueError):
            self.add("child", kind="variation", parent="missing", change="scene")

    def test_nan_budget_rejected(self):
        with self.assertRaises(ValueError):
            self.run_job(budget=float("nan"))


if __name__ == "__main__":
    unittest.main()
