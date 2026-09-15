import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_install_and_backup_existing_managed_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "sct-video-remix"
            installer.install(dest)
            (dest / "local-note.txt").write_text("preserve")
            installer.install(dest)
            self.assertTrue((dest / "SKILL.md").exists())
            backups = list((dest.parent / ".video-remix-backups").glob("*/local-note.txt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "preserve")

    def test_no_overwrite_unmanaged_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "sct-video-remix"
            dest.mkdir()
            with self.assertRaisesRegex(ValueError, "不覆盖"):
                installer.install(dest)

    def test_legacy_rename_preserves_files_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "video-remix"
            old.mkdir()
            (old / "SKILL.md").write_text("old skill")
            (old / ".video-remix-managed").write_text("1")
            (old / "local-note.txt").write_text("preserve")
            dest = installer.install(old)
            self.assertEqual(dest.name, "sct-video-remix")
            self.assertIn("name: sct-video-remix", (dest / "SKILL.md").read_text())
            self.assertFalse((old / "SKILL.md").exists())
            self.assertEqual((old / "scripts/bootstrap.py").resolve(), dest / "scripts/bootstrap.py")
            backups = list((old.parent / ".video-remix-backups").glob("*/local-note.txt"))
            self.assertEqual(backups[0].read_text(), "preserve")
            installer.install(dest)
            self.assertTrue((old / "scripts/bootstrap.py").is_file())

    def test_unmanaged_legacy_is_not_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "video-remix"
            old.mkdir()
            (old / "SKILL.md").write_text("user owned")
            installer.install(old.with_name("sct-video-remix"))
            self.assertEqual((old / "SKILL.md").read_text(), "user owned")
