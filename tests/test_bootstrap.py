import importlib.util
import contextlib
import io
import json
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
        self.assertEqual(self.ensure()["action"], "held")
        self.assertEqual(self.ensure(force=True)["revision"], second["revision"])

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

    def test_install_defaults_and_flag_environment_precedence(self):
        config = {"format": "video-remix-install.v1", "runtime_home": str(self.home), "repo": str(self.repo)}
        cases = [([], {}, self.home, str(self.repo)),
                 ([], {"VIDEO_REMIX_HOME": str(self.base / "environment")}, self.base / "environment", str(self.repo)),
                 (["--home", str(self.base / "explicit"), "--repo", "https://example.com/override.git"],
                  {"VIDEO_REMIX_HOME": str(self.base / "environment")}, self.base / "explicit", "https://example.com/override.git")]
        for flags, env, home, repo in cases:
            with self.subTest(flags=flags), patch.object(bootstrap, "read_install_config", return_value=config), \
                    patch.dict("os.environ", env, clear=True), patch.object(bootstrap, "execute_action", return_value={}) as action, \
                    contextlib.redirect_stdout(io.StringIO()):
                bootstrap.main(["check", *flags])
                self.assertEqual(action.call_args.args[2], home.resolve())
                self.assertEqual(action.call_args.args[0].repo, repo)

    def test_no_config_keeps_original_default_and_does_not_read_project_config(self):
        with patch.object(bootstrap, "read_install_config", return_value={}) as load, \
                patch.dict("os.environ", {}, clear=True), \
                patch.object(bootstrap, "execute_action", return_value={}) as action, \
                contextlib.redirect_stdout(io.StringIO()):
            bootstrap.main(["check"])
        self.assertEqual(action.call_args.args[2], Path("~/.local/share/video-remix").expanduser().resolve())
        self.assertIsNone(action.call_args.args[0].repo)
        load.assert_called_once_with(Path(bootstrap.__file__).resolve().parent.parent)

    def test_malformed_install_config_and_symlinks_fail_without_fallback(self):
        directory = self.base / "skill"
        directory.mkdir()
        config = directory / "installation.json"
        invalid = [[], {}, {"format": "video-remix-install.v1", "engine": "/untrusted/script"},
                   {"format": "video-remix-install.v1", "runtime_home": "relative"},
                   {"format": "video-remix-install.v1", "repo": "https://host/repo?token=secret"},
                   {"format": "video-remix-install.v1", "repo": "https://user:secret@host/repo"}]
        for value in invalid:
            config.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                bootstrap.read_install_config(directory)
        config.unlink()
        config.symlink_to(self.base / "missing-config")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            bootstrap.read_install_config(directory)
        with patch.object(bootstrap, "read_install_config", side_effect=ValueError("invalid config")), \
                patch.object(bootstrap, "execute_action") as action, self.assertRaises(ValueError):
            bootstrap.main(["check", "--home", str(self.home), "--repo", str(self.repo)])
        action.assert_not_called()

    def test_installed_project_route_only_recognizes_existing_public_installation(self):
        project = self.base / "project"
        project.mkdir()
        config = {"format": "video-remix-install.v1", "runtime_home": str(self.home), "repo": str(self.repo)}
        standard = self.base / ".local/share/video-remix"
        standard.mkdir(parents=True)
        bootstrap.write(standard / "runtime.json", {"repo": bootstrap.DEFAULT_REPO})
        bootstrap.write(project / "runtime-lock.json", {"repo": bootstrap.DEFAULT_REPO})
        def expand(path):
            return standard if str(path) == "~/.local/share/video-remix" else path
        with patch.object(Path, "expanduser", expand):
            route = bootstrap.installed_project_route(self.home, str(self.repo), project, config, False)
            self.assertEqual(route, (standard.resolve(), bootstrap.DEFAULT_REPO))
            self.assertEqual(bootstrap.installed_project_route(self.home, str(self.repo), None, config, False),
                             (self.home, str(self.repo)))
            self.assertEqual(bootstrap.installed_project_route(self.home, str(self.repo), project, config, True),
                             (self.home, str(self.repo)))
            bootstrap.write(project / "runtime-lock.json", {"repo": "https://unknown.invalid/repo.git"})
            self.assertEqual(bootstrap.installed_project_route(self.home, str(self.repo), project, config, False),
                             (self.home, str(self.repo)))
            bootstrap.write(project / "runtime-lock.json", {"repo": bootstrap.DEFAULT_REPO})
            bootstrap.write(standard / "runtime.json", {"repo": "https://unknown.invalid/repo.git"})
            self.assertEqual(bootstrap.installed_project_route(self.home, str(self.repo), project, config, False),
                             (self.home, str(self.repo)))


if __name__ == "__main__":
    unittest.main()
