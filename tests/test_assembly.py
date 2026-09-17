from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_remix.assembly import assemble, contract_hash, validate_contract, verify_output
from video_remix.cli import parser
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, digest, read


def contract(mode="clips", name="cut"):
    audio = {"mode": mode}
    if mode == "continuous":
        audio.update(variant="voice", **{"in": 0.1})
    return {"format": "video-remix-assembly.v1", "id": name,
            "clips": [{"variant": "red", "in": 0.2, "out": 0.7},
                      {"variant": "blue", "in": 0.1, "out": 0.6}], "audio": audio}


def asset_contract(asset="red", name="asset-cut"):
    value = contract("continuous", name)
    value["format"] = "video-remix-assembly.v2"
    value["audio"] = {"mode": "continuous", "asset": asset, "in": 0.1,
                      "provenance": "测试用独立配音素材"}
    return value


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

    def test_v2_requires_unambiguous_explicit_source_and_provenance(self):
        for audio in ({"mode": "continuous", "asset": "red"},
                      {"mode": "continuous", "asset": "red", "provenance": " "},
                      {"mode": "continuous", "asset": "red", "variant": "red", "provenance": "来源"},
                      {"mode": "continuous", "provenance": "来源"},
                      {"mode": "clips", "asset": "red", "provenance": "来源"}):
            value = asset_contract()
            value["audio"] = audio
            with self.subTest(audio=audio), self.assertRaises(ValueError):
                validate_contract(value)
        value = asset_contract()
        value["format"] = "video-remix-assembly.v1"
        with self.assertRaisesRegex(ValueError, "不支持的字段"):
            validate_contract(value)


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
        for suffix in ("wav", "mp3", "m4a"):
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-n", "-f", "lavfi",
                            "-i", "sine=frequency=660:sample_rate=48000:duration=2",
                            str(cls.assets / f"narration.{suffix}")],
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

    def test_legacy_completed_record_with_exact_v1_source_schema_reuses_without_render(self):
        value = contract("continuous")
        result = self.invoke(value, execute=True)
        legacy = {"format": "video-remix-assembly-run.v1", "status": "completed", "id": value["id"],
                  "contract_sha256": contract_hash(value), "output": result["output"],
                  "output_sha256": result["output_sha256"], "sources": {}}
        source_keys = ("variant", "task_id", "spec", "spec_path", "spec_sha256",
                       "raw_path", "raw_sha256", "run_path")
        media_keys = ("video_duration", "audio_duration", "audio_start", "video_start",
                      "width", "height", "sar", "probe")
        for name, source in result["sources"].items():
            legacy["sources"][name] = {key: source[key] for key in source_keys}
            legacy["sources"][name]["media"] = {key: source["media"][key] for key in media_keys}
        path = self.root / "assemblies/cut/run.json"
        atomic(path, legacy)
        before = path.read_bytes()
        with patch("video_remix.assembly.render") as render:
            self.assertTrue(self.invoke(value, execute=True)["reused"])
        render.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_registered_audio_formats_render_and_same_name_variant_stays_separate(self):
        for suffix in ("wav", "mp3", "m4a"):
            asset_id = "red" if suffix == "wav" else suffix
            asset = add_asset(self.root, asset_id, self.assets / f"narration.{suffix}", "voiceover")
            value = asset_contract(asset_id, name=f"audio-{suffix}")
            result = self.invoke(value, execute=True)
            self.assertEqual(result["format"], "video-remix-assembly-run.v2")
            source = result["sources"][f"asset:{asset_id}"]
            self.assertNotIn("task_id", source)
            self.assertNotIn("spec", source)
            self.assertEqual(source["asset_sha256"], asset["sha256"])
            self.assertEqual(source["provenance"], value["audio"]["provenance"])
            self.assertEqual(source["media"]["time_origin"], "first_audio_sample")
            self.assertEqual(result["sources"]["red"]["task_id"], "task-red")
            self.assertIn(str(self.root / asset["path"]), result["ffmpeg_argv"])
            self.assert_audio_frequency(self.root / result["output"], 0.1, 660)
            self.assert_audio_frequency(self.root / result["output"], 0.7, 660)
            self.assertTrue(self.invoke(value, execute=True)["reused"])

    def test_registered_video_audio_is_explicitly_used(self):
        add_asset(self.root, "voice-track", self.assets / "voice.mp4", "reference")
        value = asset_contract("voice-track")
        value["audio"]["provenance"] = "源视频音轨，显式保留原声"
        result = self.invoke(value, execute=True)
        self.assert_audio_frequency(self.root / result["output"], 0.7, 220)

    def test_delayed_audio_asset_uses_audio_origin_but_variant_rejects_misalignment(self):
        delayed = self.root / "delayed-audio.mp4"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-n",
                        "-i", str(self.assets / "voice.mp4"), "-itsoffset", "0.25",
                        "-i", str(self.assets / "narration.wav"), "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", str(delayed)],
                       check=True, capture_output=True, timeout=30)
        from video_remix.assembly_media import probe
        media = probe(delayed, shutil.which("ffprobe"))
        self.assertGreater(media["audio_start"] - media["video_start"], 0.1)
        variant = self.root / "variants/voice"
        shutil.copyfile(delayed, variant / "raw.mp4")
        run = read(variant / "run.json")
        run["output_sha256"] = digest(variant / "raw.mp4")
        atomic(variant / "run.json", run)
        with self.assertRaisesRegex(ValueError, "音轨与视频起点不同"):
            self.invoke(contract("continuous"), execute=True)
        add_asset(self.root, "delayed", delayed, "voiceover")
        value = asset_contract("delayed")
        value["audio"]["in"] = 0
        result = self.invoke(value, execute=True)
        self.assertEqual(result["sources"]["asset:delayed"]["media"]["time_origin"], "first_audio_sample")
        self.assertAlmostEqual(result["actual_audio_duration"], 1, delta=0.04)
        self.assert_audio_frequency(self.root / result["output"], 0.02, 660)
        self.assert_audio_frequency(self.root / result["output"], 0.7, 660)

    def test_v2_variant_source_still_works(self):
        value = contract("continuous")
        value["format"] = "video-remix-assembly.v2"
        result = self.invoke(value, execute=True)
        self.assert_audio_frequency(self.root / result["output"], 0.7, 220)

    def test_missing_or_silent_asset_never_falls_back_to_variant_sound(self):
        value = asset_contract("red")
        with self.assertRaisesRegex(ValueError, "必须已登记"):
            self.invoke(value, execute=True)
        add_asset(self.root, "red", self.assets / "mute.mp4", "reference")
        with self.assertRaisesRegex(ValueError, "没有音轨"):
            self.invoke(value, execute=True)
        self.assertFalse((self.root / "assemblies").exists())

    def test_asset_bounds_changes_and_escaped_registration_are_rejected(self):
        asset = add_asset(self.root, "red", self.assets / "narration.wav", "voiceover")
        value = asset_contract()
        value["audio"]["in"] = 1.1
        with self.assertRaisesRegex(ValueError, "不足以覆盖"):
            self.invoke(value)
        value["audio"]["in"] = 0.1
        self.invoke(value, execute=True)
        path = self.root / asset["path"]
        original = path.read_bytes()
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "素材已变化"):
            self.invoke(value, execute=True)
        path.write_bytes(original)
        project = read(self.root / "project.json")
        project["assets"]["red"]["path"] = str(self.assets / "narration.wav")
        atomic(self.root / "project.json", project)
        with self.assertRaisesRegex(ValueError, "逃逸"):
            self.invoke(value)

    def test_changed_asset_registration_and_provenance_do_not_reuse_completed_output(self):
        add_asset(self.root, "red", self.assets / "narration.wav", "voiceover")
        value = asset_contract()
        self.invoke(value, execute=True)
        value["audio"]["provenance"] = "变更了来源说明"
        with self.assertRaisesRegex(ValueError, "合同不同"):
            self.invoke(value, execute=True)
        project = read(self.root / "project.json")
        project["assets"]["red"]["role"] = "reference"
        atomic(self.root / "project.json", project)
        with self.assertRaisesRegex(ValueError, "来源证据已变化"):
            self.invoke(asset_contract(), execute=True)

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
