from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_remix.assembly import assemble, validate_contract, verify_output
from video_remix.cli import parser
from video_remix.project import initialize
from video_remix.storage import atomic, digest, read


def contract(mode="clips", name="cut"):
    audio = {"mode": mode}
    if mode == "continuous":
        audio.update(variant="voice", **{"in": 0.1})
    return {"format": "video-remix-assembly.v1", "id": name,
            "clips": [{"variant": "red", "in": 0.2, "out": 0.7},
                      {"variant": "blue", "in": 0.1, "out": 0.6}], "audio": audio}


class ContractTests(unittest.TestCase):
    def test_contract_rejects_nonfinite_boolean_reversed_and_path_ids(self):
        for value in (float("nan"), float("inf"), True, -1, "0"):
            value_contract = contract()
            value_contract["clips"][0]["in"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_contract(value_contract)
        for change in ({"id": "../cut"}, {"clips": []}, {"audio": {"mode": "continuous"}},
                       {"audio": {"mode": "silent", "variant": "voice"}}, {"fps": 60}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_contract({**contract(), **change})
        value_contract = contract()
        value_contract["clips"][0]["out"] = 0.1
        with self.assertRaises(ValueError):
            validate_contract(value_contract)

    def test_cli_preview_is_default(self):
        args = parser().parse_args(["assemble", "cut.json"])
        self.assertFalse(args.execute)
        self.assertTrue(parser().parse_args(["assemble", "cut.json", "--execute"]).execute)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要本地 FFmpeg")
class AssemblyMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.assets = Path(cls.media.name)
        for name, color, frequency, size in (("red", "red", 440, "96x64"),
                                              ("blue", "blue", 880, "96x64"),
                                              ("voice", "green", 220, "96x64"),
                                              ("wide", "yellow", 330, "128x64")):
            args = [shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-n", "-f", "lavfi",
                    "-i", f"color=c={color}:s={size}:r=24:d=2", "-f", "lavfi", "-i",
                    f"sine=frequency={frequency}:sample_rate=48000:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(cls.assets / f"{name}.mp4")]
            subprocess.run(args, check=True, capture_output=True, timeout=30)
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-n", "-i",
                        str(cls.assets / "red.mp4"), "-c:v", "copy", "-an", str(cls.assets / "mute.mp4")],
                       check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        initialize(self.root, "assembly test")
        for name in ("red", "blue", "voice", "wide", "mute"):
            directory = self.root / "variants" / name
            atomic(directory / "spec.json", {"id": name})
            shutil.copyfile(self.assets / f"{name}.mp4", directory / "raw.mp4")
            atomic(directory / "run.json", {"format": "video-remix-run.v1", "variant": name,
                   "phase": "downloaded", "task_id": f"task-{name}",
                   "spec_sha256": digest(directory / "spec.json"),
                   "output": f"variants/{name}/raw.mp4", "output_sha256": digest(directory / "raw.mp4")})
        self.file = self.root / "cut.json"

    def invoke(self, value=None, execute=False):
        atomic(self.file, value or contract())
        return assemble(self.root, self.file, execute=execute)

    def change_run(self, **changes):
        path = self.root / "variants/red/run.json"
        atomic(path, {**read(path), **changes})

    def test_preview_reads_evidence_without_rendering_or_directory_creation(self):
        before = digest(self.root / "variants/red/run.json")
        with patch("video_remix.assembly.subprocess.run", wraps=subprocess.run) as run:
            result = self.invoke()
        self.assertFalse(result["execute"])
        self.assertFalse((self.root / "assemblies").exists())
        self.assertTrue(all("ffprobe" in call.args[0][0] for call in run.call_args_list))
        self.assertEqual(before, digest(self.root / "variants/red/run.json"))
        self.assertIn("不能保证音色连续", result["note"])

    def test_real_render_all_audio_modes_and_idempotent_verified_reuse(self):
        original = digest(self.root / "variants/red/raw.mp4")
        for mode in ("clips", "continuous", "silent"):
            with self.subTest(mode=mode):
                value = contract(mode, name=mode)
                result = self.invoke(value, execute=True)
                self.assertEqual(result["status"], "completed")
                self.assertAlmostEqual(result["actual_duration"], 1.0, delta=1 / 30)
                streams = result["output_probe"]["streams"]
                video = next(s for s in streams if s["codec_type"] == "video")
                self.assertEqual(video["avg_frame_rate"], "30/1")
                self.assertEqual((video["width"], video["height"]), (96, 64))
                self.assertEqual(any(s["codec_type"] == "audio" for s in streams), mode != "silent")
                self.assertEqual(read(self.root / "assemblies" / mode / "contract.json"), value)
                self.assertEqual(result["sources"]["red"]["task_id"], "task-red")
                self.assertEqual(result["output_sha256"], digest(self.root / result["output"]))
                if mode != "silent":
                    self.assertAlmostEqual(result["actual_audio_duration"], 1.0, delta=0.04)
                    for timestamp, expected in ((0.1, 220 if mode == "continuous" else 440),
                                                (0.7, 220 if mode == "continuous" else 880)):
                        self.assert_audio_frequency(self.root / result["output"], timestamp, expected)
                self.assertTrue(self.invoke(value, execute=True)["reused"])
                self.assertFalse((self.root / "assemblies" / mode / "partial.mp4").exists())
        self.assertEqual(original, digest(self.root / "variants/red/raw.mp4"))

    def assert_audio_frequency(self, path, timestamp, expected):
        samples = subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-ss", str(timestamp),
                                  "-i", str(path), "-t", "0.1", "-vn", "-ac", "1", "-ar", "48000",
                                  "-f", "f32le", "pipe:1"], check=True, capture_output=True, timeout=30).stdout
        values = struct.unpack(f"<{len(samples) // 4}f", samples)
        crossings = sum(left <= 0 < right for left, right in zip(values, values[1:]))
        self.assertAlmostEqual(crossings / (len(values) / 48000), expected, delta=20)

    def test_short_output_audio_is_rejected(self):
        result = self.invoke(execute=True)
        from video_remix.assembly_media import probe
        media = probe(self.root / result["output"], shutil.which("ffprobe"))
        media["audio_duration"] = 0.5
        with self.assertRaisesRegex(ValueError, "音轨时长"):
            verify_output(media, result["contract"], result["sources"], 1.0)

    def test_completed_assembly_reusable_after_project_is_moved(self):
        self.invoke(execute=True)
        moved = self.root.parent / (self.root.name + "-moved")
        self.root.rename(moved)
        self.addCleanup(shutil.rmtree, moved)
        self.root, self.file = moved, moved / "cut.json"
        self.assertTrue(self.invoke(execute=True)["reused"])

    def test_real_cut_order_uses_selected_frames(self):
        result = self.invoke(contract("silent"), execute=True)
        for seconds, dominant in ((0.1, 0), (0.8, 2)):
            sample = subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-ss", str(seconds),
                                     "-i", str(self.root / result["output"]), "-frames:v", "1",
                                     "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                                    check=True, capture_output=True, timeout=30).stdout
            self.assertGreater(sample[dominant], 200)

    def test_hash_status_identity_and_paths_must_match_downloaded_variant(self):
        original = read(self.root / "variants/red/run.json")
        for changes in ({"phase": "polling"}, {"spec_sha256": "bad"}, {"output_sha256": "bad"},
                        {"task_id": None}, {"variant": "blue"}, {"output": "../outside.mp4"},
                        {"output": "variants/blue/raw.mp4"}):
            self.change_run(**{**original, **changes})
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.invoke()
        self.change_run(**original)
        atomic(self.root / "variants/red/spec.json", {"id": "red", "changed": True})
        with self.assertRaisesRegex(ValueError, "spec"):
            self.invoke()

    def test_bounds_resolution_audio_and_continuous_length_are_strict(self):
        cases = []
        for variant in ("wide", "mute"):
            value = contract()
            value["clips"][0]["variant"] = variant
            cases.append(value)
        value = contract()
        value["clips"][0]["out"] = 3
        cases.append(value)
        value = contract("continuous")
        value["audio"]["in"] = 1.5
        cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.invoke(value)
        value = contract("silent")
        value["clips"][0]["variant"] = "mute"
        self.assertFalse(self.invoke(value)["execute"])

    def test_assembly_parent_symlink_rejected(self):
        (self.root / "assemblies").symlink_to(self.root / "variants", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "逃逸"):
            self.invoke(execute=True)

    def test_changed_contract_and_output_never_overwritten(self):
        result = self.invoke(execute=True)
        value = contract()
        value["clips"][0]["in"] = 0.3
        with self.assertRaisesRegex(ValueError, "合同不同"):
            self.invoke(value, execute=True)
        (self.root / result["output"]).write_bytes(b"changed output")
        with self.assertRaisesRegex(ValueError, "输出已被修改"):
            self.invoke(execute=True)

    def test_render_failure_keeps_contract_diagnostic_and_disallows_blind_retry(self):
        real_run = subprocess.run

        def fail_render(args, **kwargs):
            if Path(args[0]).name == "ffmpeg":
                Path(args[-1]).write_bytes(b"partial evidence")
                return subprocess.CompletedProcess(args, 1, "", "local test failure")
            return real_run(args, **kwargs)

        with patch("video_remix.assembly.subprocess.run", side_effect=fail_render), self.assertRaises(RuntimeError):
            self.invoke(execute=True)
        directory = self.root / "assemblies/cut"
        self.assertEqual(read(directory / "run.json")["status"], "failed")
        self.assertIn("local test failure", read(directory / "ffmpeg.json")["stderr"])
        self.assertTrue((directory / "partial.mp4").exists())
        self.assertFalse((directory / "output.mp4").exists())
        with self.assertRaisesRegex(ValueError, "不自动重试"):
            self.invoke(execute=True)
