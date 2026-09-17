from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_remix.cli import parser
from video_remix.production_job import produce
from video_remix.project import add_asset, add_variant, initialize
from video_remix.storage import atomic, digest, locked, read


class Provider:
    """仅模拟平台，下载与本地拼接使用真实可解码视频。"""
    def __init__(self, media):
        self.media, self.submissions, self.queries = media, [], []
        self.status, self.price, self.ambiguous = "success", 24, False

    def account(self):
        return {"credits": 200}

    def preflight(self, items):
        return None

    def arguments(self, spec, root, assets):
        return [spec["id"], spec["prompt"]]

    def submit(self, args):
        self.submissions.append(args)
        if self.ambiguous:
            raise TimeoutError("unknown submission")
        return {"submit_id": f"task-{len(self.submissions)}", "credit_count": self.price}

    def query(self, task_id):
        self.queries.append(task_id)
        return {"submit_id": task_id, "gen_status": self.status, "credit_count": self.price}

    def download(self, task_id, directory):
        target = directory / "raw.mp4"
        shutil.copyfile(self.media, target)
        return target


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要本地FFmpeg")
class ProductionJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.TemporaryDirectory()
        cls.media = Path(cls.media_dir.name) / "fixture.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                        "testsrc2=size=96x64:rate=30:duration=4", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(cls.media)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        initialize(self.root, "production test")
        product = self.root / "product.png"
        product.write_bytes(b"image fixture")
        add_asset(self.root, "product", product, "商品")
        for name in ("first", "second"):
            file = self.root / f"{name}.json"
            atomic(file, {"id": name, "kind": "replica", "provider": "dreamina",
                "model": "seedance2.0fast_vip", "duration": 4, "ratio": "9:16", "resolution": "720p",
                "inputs": [{"type": "image", "asset": "product", "role": "产品"}], "prompt": "literal action"})
            add_variant(self.root, file)
        self.job = {"format": "video-remix-production.v1", "id": "film", "budget": 48,
            "estimate_per_job": 24, "assembly": {"format": "video-remix-assembly.v1", "id": "cut",
            "clips": [{"variant": "first", "in": 0, "out": 2},
                      {"variant": "second", "in": 0, "out": 2}], "audio": {"mode": "clips"}}}
        self.file = self.root / "production.json"
        self.provider = Provider(self.media)
        disk = patch("video_remix.runner.disk_check")
        disk.start()
        self.addCleanup(disk.stop)

    def invoke(self, execute=True):
        atomic(self.file, self.job)
        return produce(self.root, self.file, execute=execute, provider=self.provider)

    def test_preview_has_no_paid_calls_or_job_records(self):
        result = self.invoke(False)
        self.assertFalse(result["execute"])
        self.assertEqual(len(result["generation"]["variants"]), 2)
        self.assertEqual(self.provider.submissions, [])
        self.assertFalse((self.root / "productions").exists())
        self.assertFalse(parser().parse_args(["produce", "job.json"]).execute)

    def test_pending_resumes_to_real_film_without_resubmit_and_completed_reuses(self):
        self.provider.status = "generating"
        result = self.invoke()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(self.provider.submissions), 1)
        self.provider.status = "success"
        result = self.invoke()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.provider.submissions), 2)
        output = self.root / result["output"]
        before = digest(output)
        result = self.invoke()
        self.assertTrue(result["assembly"]["reused"])
        self.assertEqual(len(self.provider.submissions), 2)
        self.assertEqual(digest(output), before)
        self.assertEqual(result["assembly"]["frame_grid"]["actual_frames"], 120)

    def test_budget_counts_previously_submitted_and_cannot_be_silently_raised(self):
        self.job["budget"] = 24
        result = self.invoke()
        self.assertEqual(result["status"], "budget")
        self.assertEqual(len(self.provider.submissions), 1)
        self.assertEqual(self.invoke()["status"], "budget")
        self.assertEqual(len(self.provider.submissions), 1)
        self.job["budget"] = 48
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.invoke()

    def test_failed_and_ambiguous_do_not_regenerate(self):
        self.provider.status = "fail"
        self.assertEqual(self.invoke()["status"], "failed")
        self.assertEqual(self.invoke()["status"], "failed")
        self.assertEqual(len(self.provider.submissions), 1)

    def test_ambiguous_remains_original_task(self):
        self.provider.ambiguous = True
        with self.assertRaises(TimeoutError):
            self.invoke()
        with self.assertRaisesRegex(ValueError, "结果不明"):
            self.invoke()
        self.assertEqual(len(self.provider.submissions), 1)

    def test_contract_or_spec_change_refused_before_next_submission(self):
        self.provider.status = "generating"
        self.invoke()
        self.job["assembly"]["clips"][0]["out"] = 1
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.invoke()
        self.job["assembly"]["clips"][0]["out"] = 2
        spec = self.root / "variants/second/spec.json"
        atomic(spec, {**read(spec), "prompt": "changed"})
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.invoke()
        self.assertEqual(len(self.provider.submissions), 1)

    def test_invalid_bounds_shapes_audio_and_escape_never_pay(self):
        original = deepcopy(self.job)
        for changes in ({"out": 5}, {"variant": "../outside"}, {"out": 0.01}):
            self.job = deepcopy(original)
            self.job["assembly"]["clips"][0].update(changes)
            with self.assertRaises(ValueError):
                self.invoke()
        self.job = deepcopy(original)
        self.job["assembly"]["audio"] = {"mode": "continuous", "variant": "first", "in": 1}
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            self.invoke()
        self.assertEqual(self.provider.submissions, [])

    def test_single_clip_and_duplicate_variant_generates_once(self):
        self.job["assembly"]["clips"] = [self.job["assembly"]["clips"][0]]
        result = self.invoke()
        self.assertEqual(result["assembly"]["frame_grid"]["actual_frames"], 60)
        self.job["id"], self.job["assembly"]["id"] = "repeat", "repeat-cut"
        self.job["assembly"]["clips"] *= 2
        result = self.invoke()
        self.assertEqual(len(self.provider.submissions), 1)
        self.assertEqual(result["assembly"]["frame_grid"]["actual_frames"], 120)

    def test_lock_and_symlink_do_not_cross_project(self):
        with locked(self.root / ".production.lock"):
            with self.assertRaisesRegex(ValueError, "已有进程"):
                self.invoke()
        outside = Path(self.temp.name).parent / (self.root.name + "-outside")
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        (self.root / "productions").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "逃逸"):
            self.invoke()
        self.assertEqual(self.provider.submissions, [])

    def test_unusable_assembly_slot_is_detected_before_payment(self):
        from video_remix.assembly import contract_hash
        directory = self.root / "assemblies/cut"
        atomic(directory / "contract.json", self.job["assembly"])
        atomic(directory / "run.json", {"status": "failed",
               "contract_sha256": contract_hash(self.job["assembly"])})
        with self.assertRaisesRegex(ValueError, "未完成或失败"):
            self.invoke()
        self.assertEqual(self.provider.submissions, [])

    def test_missing_media_tools_does_not_pay(self):
        with patch("video_remix.production_job.media_tools", side_effect=ValueError("missing tool")):
            with self.assertRaisesRegex(ValueError, "missing tool"):
                self.invoke()
        self.assertEqual(self.provider.submissions, [])

    def test_downloaded_short_source_blocks_remaining_payment(self):
        spec = self.root / "variants/first/spec.json"
        atomic(spec, {**read(spec), "duration": 5})
        self.job["assembly"]["clips"][0]["out"] = 5
        with self.assertRaisesRegex(ValueError, "超出原始视频时长"):
            self.invoke()
        self.assertEqual(len(self.provider.submissions), 1)
        with self.assertRaisesRegex(ValueError, "超出原始视频时长"):
            self.invoke()
        self.assertEqual(len(self.provider.submissions), 1)
