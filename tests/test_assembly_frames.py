"""真实不同帧率素材的帧栅格、尾部和连续声音回归。"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_remix.assembly import assemble
from video_remix.assembly_media import frame_grid, frame_number, probe
from video_remix.project import add_asset, initialize
from video_remix.storage import atomic, digest, read


COUNTS = [101, 116, 38, 184, 97, 85, 79]


def register(root, name, path):
    directory = root / "variants" / name
    atomic(directory / "spec.json", {"id": name})
    shutil.copyfile(path, directory / "raw.mp4")
    atomic(directory / "run.json", {"format": "video-remix-run.v1", "variant": name,
           "phase": "downloaded", "task_id": f"task-{name}",
           "spec_sha256": digest(directory / "spec.json"), "output": f"variants/{name}/raw.mp4",
           "output_sha256": digest(directory / "raw.mp4")})


class FrameRoundingTests(unittest.TestCase):
    def test_exact_half_rounds_up_and_cumulative_boundaries_do_not_drift(self):
        self.assertEqual(frame_number(0.05), 2)
        self.assertEqual(frame_number(0.049999), 1)
        value = {"clips": [{"variant": "v", "in": 0, "out": 0.05}] * 7}
        grid = frame_grid(value, {"v": {"media": {"video_duration": 8}}})
        self.assertEqual([clip["frames"] for clip in grid["clips"]], [2, 1, 2, 1, 2, 1, 2])
        self.assertEqual(grid["expected_frames"], 11)
        self.assertAlmostEqual(grid["requested_duration"], 0.35)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要本地 FFmpeg")
class FrameGridMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.TemporaryDirectory()
        cls.assets = Path(cls.temp_media.name)
        for rate in ("24", "25", "30", "irregular"):
            for color in ("red", "blue"):
                native = "60" if rate == "irregular" else rate
                args = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                        f"color=c={color}:s=32x32:r={native}:d=8", "-an"]
                if rate == "irregular":
                    args += ["-frames:v", "361", "-vf", "setpts=round(N*60/24)",
                             "-fps_mode", "passthrough", "-enc_time_base", "1/60",
                             "-video_track_timescale", "60"]
                args += ["-c:v", "libx264", str(cls.assets / f"{rate}-{color}.mp4")]
                subprocess.run(args, check=True, capture_output=True, timeout=30)
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                        "sine=frequency=660:sample_rate=48000:duration=26", str(cls.assets / "voice.wav")],
                       check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp_media.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        initialize(self.root, "frame grid")
        add_asset(self.root, "voice", self.assets / "voice.wav", "voiceover")

    def invoke(self, clips, name):
        value = {"format": "video-remix-assembly.v2", "id": name, "clips": clips,
                 "audio": {"mode": "continuous", "asset": "voice", "in": 0, "provenance": "测试音轨"}}
        path = self.root / "assembly.json"
        atomic(path, value)
        return assemble(self.root, path, execute=True)

    def color_runs(self, output):
        data = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-an", "-vf", "scale=1:1",
                               "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                              capture_output=True, check=True, timeout=30).stdout
        runs, previous = [], None
        for offset in range(0, len(data), 3):
            color = data[offset] > data[offset + 2]
            if color == previous:
                runs[-1] += 1
            else:
                runs.append(1)
                previous = color
        return runs

    def test_seven_cuts_are_exactly_700_frames_for_real_native_rates(self):
        for rate in ("24", "25", "30", "irregular"):
            clips = []
            for index, count in enumerate(COUNTS):
                name = f"v-{rate}-{index}"
                color = "red" if index % 2 == 0 else "blue"
                register(self.root, name, self.assets / f"{rate}-{color}.mp4")
                clips.append({"variant": name, "in": 0, "out": count / 30})
            with self.subTest(rate=rate):
                result = self.invoke(clips, f"cut-{rate}")
                grid = result["frame_grid"]
                self.assertTrue(grid["verified"])
                self.assertEqual(grid["actual_frames"], 700)
                self.assertEqual([clip["output_end_frame"] for clip in grid["clips"]],
                                 [101, 217, 255, 439, 536, 621, 700])
                self.assertEqual(self.color_runs(self.root / result["output"]), COUNTS)
                self.assertAlmostEqual(result["actual_duration"], 700 / 30, places=5)
                self.assertAlmostEqual(result["actual_audio_duration"], 700 / 30, delta=0.022)
        media = probe(self.assets / "irregular-red.mp4", shutil.which("ffprobe"))
        stream = media["probe"]["streams"][0]
        self.assertEqual(stream["nb_frames"], "361")
        self.assertEqual(stream["avg_frame_rate"], "21660/901")
        self.assertAlmostEqual(media["video_duration"], 15.016667, places=6)

    def test_non_grid_total_uses_cumulative_rounding_without_stretching_audio(self):
        register(self.root, "v", self.assets / "24-red.mp4")
        result = self.invoke([{"variant": "v", "in": 0.05, "out": 0.1}] * 7, "half-frame")
        self.assertEqual(result["frame_grid"]["actual_frames"], 11)
        self.assertEqual(result["frame_grid"]["clips"][0]["source_start_frame"], 2)
        self.assertAlmostEqual(result["actual_audio_duration"], 0.35, delta=0.001)
        self.assertAlmostEqual(result["actual_duration"], 11 / 30, places=5)

    def test_non_grid_source_end_rounds_naturally_and_out_of_bounds_never_pads(self):
        register(self.root, "v", self.assets / "irregular-red.mp4")
        source_duration = probe(self.assets / "irregular-red.mp4", shutil.which("ffprobe"))["video_duration"]
        result = self.invoke([{"variant": "v", "in": 0, "out": source_duration}], "whole-source")
        self.assertEqual(result["frame_grid"]["actual_frames"], 451)
        self.assertAlmostEqual(result["actual_audio_duration"], source_duration, delta=0.022)
        with self.assertRaisesRegex(ValueError, "超出原始"):
            self.invoke([{"variant": "v", "in": 0, "out": source_duration + 0.04}], "over-end")
        self.assertFalse((self.root / "assemblies/over-end").exists())

    def test_quantized_tail_without_enough_source_stops_before_render(self):
        register(self.root, "v", self.assets / "24-red.mp4")
        with self.assertRaisesRegex(ValueError, "不补尾帧"):
            self.invoke([{"variant": "v", "in": 7.95, "out": 8}], "short-tail")
        self.assertFalse((self.root / "assemblies/short-tail").exists())

    def test_decode_shortage_fails_and_preserves_partial_instead_of_padding(self):
        register(self.root, "v", self.assets / "24-red.mp4")

        def overstated_probe(path, executable):
            data = probe(path, executable)
            if Path(path).name == "raw.mp4":
                data["video_duration"] = 10
            return data

        with patch("video_remix.assembly.probe", side_effect=overstated_probe), self.assertRaisesRegex(
                ValueError, "实际解码帧数不符"):
            self.invoke([{"variant": "v", "in": 0, "out": 9}], "decode-short")
        directory = self.root / "assemblies/decode-short"
        self.assertTrue((directory / "partial.mp4").exists())
        self.assertFalse((directory / "output.mp4").exists())
        self.assertEqual(read(directory / "run.json")["status"], "failed")

    def test_quantized_video_never_relaxes_continuous_audio_bounds(self):
        register(self.root, "v", self.assets / "30-red.mp4")
        clips = [{"variant": "v", "in": 0, "out": 8}] * 3 + [{"variant": "v", "in": 0, "out": 2.001}]
        with self.assertRaisesRegex(ValueError, "音轨不足"):
            self.invoke(clips, "audio-short")
        self.assertFalse((self.root / "assemblies/audio-short").exists())
