import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BOOTSTRAP = Path(__file__).resolve().parents[1] / "skills/video-remix/scripts/bootstrap.py"
spec = importlib.util.spec_from_file_location("bootstrap", BOOTSTRAP)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "upstream"
        self.repo.mkdir()
        self.home = self.base / "runtime"
        self.git("init", "-b", "main")
        (self.repo / "video_remix").mkdir()
        (self.repo / "video_remix/__init__.py").write_text("")
        (self.repo / "video_remix/cli.py").write_text('def main():\n print("healthy")\n return 0\n')
        guide = self.repo / "skills/video-remix/references/workflow.md"
        guide.parent.mkdir(parents=True)
        guide.write_text("version one")
        (self.repo / ".gitignore").write_text("__pycache__/\n")
        self.commit()

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def ensure(self, **kwargs):
        return bootstrap.resolve(self.home, str(self.repo), **kwargs)

    def test_download_update_and_rollback_keep_old_engine(self):
        first = self.ensure()
        (self.repo / "video_remix/cli.py").write_text('def main():\n print("version two")\n return 0\n')
        sha = self.commit()
        check = self.ensure(check=True)
        self.assertTrue(check["update_available"])
        self.assertEqual(bootstrap.read(self.home / "runtime.json")["current"]["revision"], first["revision"])
        second = self.ensure()
        self.assertEqual(second["revision"], sha)
        self.assertTrue(Path(first["engine"]).is_dir())
        rollback = self.ensure(rollback=True)
        self.assertEqual(rollback["revision"], first["revision"])
        self.assertEqual(self.ensure(offline=True)["revision"], first["revision"])

    def test_corrupt_local_engine_repaired_in_new_directory(self):
        first = self.ensure()
        file = Path(first["engine"]) / "video_remix/cli.py"
        file.write_text("BROKEN CODE")
        repaired = self.ensure()
        self.assertNotEqual(repaired["engine"], first["engine"])
        self.assertEqual(file.read_text(), "BROKEN CODE")
        self.assertEqual(repaired["revision"], first["revision"])

    def test_bad_update_preserves_current(self):
        first = self.ensure()
        (self.repo / "video_remix/cli.py").write_text("raise RuntimeError('bad update')")
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "检查失败"):
            self.ensure()
        self.assertEqual(self.ensure(offline=True)["engine"], first["engine"])

    def test_offline_fallback_and_cold_start(self):
        with self.assertRaises(RuntimeError):
            self.ensure(offline=True)
        first = self.ensure()
        with patch.object(bootstrap, "newest", side_effect=RuntimeError("network")):
            self.assertEqual(self.ensure()["engine"], first["engine"])
            self.assertEqual(self.ensure()["action"], "offline_fallback")

    def test_update_does_not_touch_external_project(self):
        project = self.base / "my-assets"
        project.mkdir()
        file = project / "raw.mp4"
        file.write_bytes(b"keep me")
        self.ensure()
        self.assertEqual(file.read_bytes(), b"keep me")

    def test_reject_accidental_switch_of_trusted_remote(self):
        self.ensure()
        with self.assertRaisesRegex(RuntimeError, "另一仓库"):
            bootstrap.resolve(self.home, "https://example.invalid/other.git")


if __name__ == "__main__":
    unittest.main()
