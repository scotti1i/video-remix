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
        self.query_status = "success"
        self.query_extra = {}

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
        return {"submit_id": task_id, "gen_status": self.query_status, "credit_count": self.price,
                "prompt": 'literal $HOME `echo test`\nKeep this newline.\n', **self.query_extra}

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

    def test_pending_resume_ignores_missing_inputs_and_submission_dependencies(self):
        provider = FakeDreamina()
        provider.query_status = "querying"
        self.run_job(provider=provider)
        asset = read(self.root / "project.json")["assets"]["product"]
        (self.root / asset["path"]).unlink()
        with patch.object(provider, "account", side_effect=RuntimeError("account unavailable")), \
                patch.object(provider, "preflight", side_effect=RuntimeError("model retired")), \
                patch.object(provider, "arguments", side_effect=RuntimeError("inputs unavailable")), \
                patch("video_remix.runner.disk_check", side_effect=ValueError("disk full")):
            result = self.run_job(provider=provider)
        self.assertEqual(result["stop"], "pending")
        self.assertEqual(provider.queries, ["task-1", "task-1"])
        self.assertEqual(len(provider.submissions), 1)

    def test_downloaded_result_works_without_provider_or_inputs(self):
        self.run_job()
        asset = read(self.root / "project.json")["assets"]["product"]
        (self.root / asset["path"]).unlink()
        with patch("video_remix.runner.Dreamina", side_effect=ValueError("CLI missing")), \
                patch("video_remix.runner.disk_check", side_effect=ValueError("disk full")):
            result = execute(self.root, ["replica"], 100, 42, execute=True)
        self.assertEqual(result["results"][0]["phase"], "downloaded")

    def test_missing_raw_downloads_original_task_without_account_or_inputs(self):
        provider = FakeDreamina()
        self.run_job(provider=provider)
        (self.root / "variants/replica/raw.mp4").unlink()
        asset = read(self.root / "project.json")["assets"]["product"]
        (self.root / asset["path"]).unlink()
        with patch.object(provider, "account", side_effect=RuntimeError("account unavailable")), \
                patch.object(provider, "preflight", side_effect=RuntimeError("model retired")):
            result = self.run_job(provider=provider)
        self.assertEqual(result["results"][0]["phase"], "downloaded")
        self.assertEqual(provider.queries, ["task-1", "task-1"])
        self.assertEqual(len(provider.submissions), 1)

    def test_modified_raw_still_blocks_recovery(self):
        provider = FakeDreamina()
        self.run_job(provider=provider)
        (self.root / "variants/replica/raw.mp4").write_bytes(b"changed result")
        with self.assertRaisesRegex(ValueError, "原始视频已被修改"):
            self.run_job(provider=provider)
        self.assertEqual(provider.queries, ["task-1"])

    def test_download_resume_checks_disk_and_ffprobe_before_download(self):
        provider = FakeDreamina()
        provider.query_status = "querying"
        self.run_job(provider=provider)
        provider.query_status = "success"
        with patch.object(provider, "download") as download:
            with patch("video_remix.runner.disk_check", side_effect=ValueError("disk full")), \
                    self.assertRaisesRegex(ValueError, "disk full"):
                self.run_job(provider=provider)
            with patch("video_remix.runner.shutil.which", return_value=None), \
                    self.assertRaisesRegex(ValueError, "ffprobe"):
                self.run_job(provider=provider)
            download.assert_not_called()
        self.assertEqual(len(provider.submissions), 1)

    def test_ambiguous_resume_blocks_without_submission_dependencies(self):
        provider = FakeDreamina(ambiguous=True)
        with self.assertRaises(TimeoutError):
            self.run_job(provider=provider)
        with patch.object(provider, "account", side_effect=RuntimeError("account unavailable")), \
                self.assertRaisesRegex(ValueError, "结果不明"):
            self.run_job(provider=provider)
        self.assertEqual(len(provider.submissions), 1)

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

    def test_batch_waits_for_download_before_submitting_next(self):
        self.add("variation", kind="variation", parent="replica", change="场景")
        provider = FakeDreamina()
        provider.query_status = "querying"
        provider.query_extra = {"queue_info": {"queue_status": "Queueing", "queue_idx": 59446,
                                               "queue_length": 544491, "debug_info": "private"}}
        ids = ["replica", "variation"]
        first = self.run_job(ids, provider=provider)
        self.assertEqual(first["stop"], "pending")
        self.run_job(ids, provider=provider)
        self.assertEqual(len(provider.submissions), 1)
        self.assertEqual(provider.queries, ["task-1", "task-1"])
        self.assertFalse((self.root / "variants/variation/run.json").exists())
        record = read(self.root / "variants/replica/run.json")
        self.assertEqual(record["queue_info"]["queue_status"], "Queueing")
        self.assertNotIn("debug_info", record["queue_info"])
        provider.query_status, provider.query_extra = "success", {}
        self.run_job(ids, provider=provider)
        self.assertEqual(len(provider.submissions), 2)
        self.assertEqual(provider.queries[-2:], ["task-1", "task-2"])
        self.assertEqual(read(self.root / "variants/replica/run.json")["phase"], "downloaded")
        self.assertEqual(read(self.root / "variants/variation/run.json")["phase"], "downloaded")

    def test_platform_fail_stops_batch_and_never_requeries(self):
        self.add("variation", kind="variation", parent="replica", change="场景")
        provider = FakeDreamina()
        provider.query_status = "fail"
        provider.query_extra = {"fail_reason": "api error: ret=1310, message=ExceedConcurrencyLimit token=secret"}
        with patch("video_remix.runner.time.sleep") as sleep:
            result = execute(self.root, ["replica", "variation"], 100, 42,
                             execute=True, wait=30, provider=provider)
        self.assertEqual(result["stop"], "failed")
        sleep.assert_not_called()
        self.run_job(["replica", "variation"], provider=provider)
        self.assertEqual(len(provider.submissions), 1)
        self.assertEqual(provider.queries, ["task-1"])
        record = read(self.root / "variants/replica/run.json")
        self.assertEqual(record["phase"], "failed")
        self.assertEqual(record["error_class"], "concurrency_limit")
        self.assertEqual(record["provider_error_code"], 1310)
        self.assertNotIn("secret", json.dumps(record))

    def test_old_polling_fail_record_is_terminal_without_network_query(self):
        provider = FakeDreamina()
        provider.query_status = "querying"
        self.run_job(provider=provider)
        path = self.root / "variants/replica/run.json"
        record = read(path)
        record["provider_status"] = "fail"
        atomic(path, record)
        self.assertEqual(self.run_job(provider=provider)["stop"], "failed")
        self.assertEqual(provider.queries, ["task-1"])
        self.assertEqual(read(path)["phase"], "failed")

    def test_queue_snapshot_tracks_generating_and_clears_missing_data(self):
        provider = FakeDreamina()
        provider.query_status = "querying"
        provider.query_extra = {"queue_info": {"queue_status": "Generating", "queue_idx": 0}}
        self.run_job(provider=provider)
        path = self.root / "variants/replica/run.json"
        self.assertEqual(read(path)["queue_info"]["queue_status"], "Generating")
        provider.query_extra = {}
        self.run_job(provider=provider)
        self.assertIsNone(read(path)["queue_info"])

    def test_query_connection_loss_does_not_resubmit_or_advance_batch(self):
        self.add("variation", kind="variation", parent="replica", change="场景")
        provider = FakeDreamina()
        with patch.object(provider, "query", side_effect=TimeoutError("network lost")):
            with self.assertRaises(TimeoutError):
                self.run_job(["replica", "variation"], provider=provider)
        self.assertEqual(len(provider.submissions), 1)
        self.assertEqual(read(self.root / "variants/replica/run.json")["task_id"], "task-1")
        self.run_job(["replica", "variation"], provider=provider)
        self.assertEqual(len(provider.submissions), 2)
        self.assertEqual(provider.queries, ["task-1", "task-2"])

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

    def test_new_rerun_requires_identical_generation_conditions(self):
        self.add("same", kind="rerun", parent="replica")
        changes = {"model": "seedance2.0_vip", "duration": 15, "ratio": "16:9",
                   "resolution": "1080p", "prompt": "different copy",
                   "inputs": [{"type": "image", "asset": "product", "role": "different role"}]}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "rerun 必须保持"):
                self.add("changed-" + field, kind="rerun", parent="replica", **{field: value})
        self.add("changed-variation", kind="variation", parent="replica",
                 prompt="different copy", change="原创台词")

    def test_legacy_changed_rerun_can_resume_without_new_registration_rules(self):
        directory = self.root / "variants/legacy"
        atomic(directory / "spec.json", {**self.spec, "id": "legacy", "kind": "rerun",
                                          "parent": "replica", "prompt": "legacy changed copy"})
        provider = FakeDreamina()
        provider.query_status = "querying"
        self.run_job(["legacy"], provider=provider)
        asset = read(self.root / "project.json")["assets"]["product"]
        (self.root / asset["path"]).unlink()
        provider.query_status = "success"
        result = self.run_job(["legacy"], provider=provider)
        self.assertEqual(result["results"][0]["phase"], "downloaded")
        self.assertEqual(len(provider.submissions), 1)

    def test_nan_budget_rejected(self):
        with self.assertRaises(ValueError):
            self.run_job(budget=float("nan"))


if __name__ == "__main__":
    unittest.main()
