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
            dest = Path(directory) / "video-remix"
            installer.install(dest)
            (dest / "local-note.txt").write_text("preserve")
            installer.install(dest)
            self.assertTrue((dest / "SKILL.md").exists())
            backups = list((dest.parent / ".video-remix-backups").glob("*/local-note.txt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "preserve")

    def test_no_overwrite_unmanaged_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "video-remix"
            dest.mkdir()
            with self.assertRaisesRegex(ValueError, "不覆盖"):
                installer.install(dest)
