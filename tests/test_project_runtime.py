import json
import os
import contextlib
import io
import selectors
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_bootstrap import bootstrap
from test_install import installer


SOURCE = Path(__file__).resolve().parents[1]


class ProjectRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo, self.home, self.project = (self.base / n for n in ("repo", "home", "project"))
        self.repo.mkdir()
        for name in ("video_remix", "skills"):
            shutil.copytree(SOURCE / name, self.repo / name, ignore=shutil.ignore_patterns("__pycache__"))
        (self.repo / ".gitignore").write_text("__pycache__/\n")
        self.git("init", "-b", "main")
        self.first = self.commit()
        self.call("run", "--", "init", str(self.project), "--name", "Test")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "test")
        return self.git("rev-parse", "HEAD")

    def newer(self):
        (self.repo / "change.txt").write_text("new version")
        return self.commit()

    def call(self, action, *args, success=True, home=None, cwd=None):
        script = SOURCE / "skills/video-remix/scripts/bootstrap.py"
        result = subprocess.run([sys.executable, str(script), action, "--home", str(home or self.home),
                                 "--repo", str(self.repo), *args], cwd=cwd or self.base,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return json.loads(result.stdout if success else result.stderr)

    def pin(self):
        return bootstrap.read(self.project / "runtime-lock.json")

    def test_global_update_keeps_project_version_and_nested_cwd(self):
        self.assertEqual(self.pin()["revision"], self.first)
        latest = self.newer()
        self.assertEqual(self.call("update")["revision"], latest)
        nested = self.project / "nested"
        nested.mkdir()
        selected = self.call("ensure", cwd=nested)
        self.assertEqual(selected["revision"], self.first)
        self.call("run", "--", "--project", str(self.project), "status")
        self.assertEqual(self.pin()["revision"], self.first)

    def test_compatible_upgrade_preview_backup_and_apply(self):
        old_data = (self.project / "project.json").read_bytes()
        latest = self.newer()
        preview = self.call("project-upgrade", "--project", str(self.project))
        self.assertFalse(preview["applied"])
        self.assertEqual(self.pin()["revision"], self.first)
        applied = self.call("project-upgrade", "--project", str(self.project), "--apply")
        self.assertTrue(applied["applied"])
        self.assertEqual(self.pin()["revision"], latest)
        backup = Path(applied["backup"])
        self.assertEqual(bootstrap.read(backup / "runtime-lock.json")["revision"], self.first)
        self.assertEqual((backup / "project.json").read_bytes(), old_data)
        self.assertEqual((self.project / "project.json").read_bytes(), old_data)
        self.call("run", "--project", str(self.project), "--", "status")

    def test_incompatible_update_keeps_old_project_usable(self):
        file = self.repo / "video_remix/compatibility.py"
        file.write_text('def inspect(root):\n raise ValueError("unsupported old format")\n')
        self.commit()
        old_pin = self.pin()
        self.call("project-upgrade", "--project", str(self.project), "--apply", success=False)
        self.assertEqual(self.pin(), old_pin)
        self.call("run", "--project", str(self.project), "--", "status")

    def test_pending_and_uncertain_jobs_block_upgrade(self):
        self.newer()
        record = self.project / "variants/test/run.json"
        record.parent.mkdir(parents=True)
        for phase in ("submitted", "submit_intent", "polling", "download_pending"):
            bootstrap.write(record, {"phase": phase})
            error = self.call("project-upgrade", "--project", str(self.project), "--apply", success=False)
            self.assertIn("未完成", error["error"])
            self.assertEqual(self.pin()["revision"], self.first)

    def test_running_process_blocks_upgrade(self):
        with bootstrap.lock(self.project, ".runtime.lock"):
            self.call("project-upgrade", "--project", str(self.project), "--apply", success=False)
        with bootstrap.lock(self.project, ".project.lock"):
            self.call("project-upgrade", "--project", str(self.project), "--apply", success=False)

    @contextlib.contextmanager
    def holding_runtime_process(self, action):
        # 在真实启动器锁内暂停工作体，验证操作系统跨进程锁的作用域。
        code = ("import importlib.util,sys\n"
                "spec=importlib.util.spec_from_file_location('bootstrap',sys.argv[1])\n"
                "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n"
                "def hold(*args):\n print('locked',flush=True);sys.stdin.readline();return {}\n"
                "module.execute_action=hold\nmodule.main(sys.argv[2:])\n")
        script = SOURCE / "skills/video-remix/scripts/bootstrap.py"
        args = [sys.executable, "-c", code, str(script), action, "--home", str(self.home),
                "--repo", str(self.repo), "--project", str(self.project)]
        if action == "run":
            args += ["--", "status"]
        process = subprocess.Popen(args, cwd=self.base, text=True, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=10), "子进程未取得运行锁")
            self.assertEqual(process.stdout.readline().strip(), "locked")
            yield process
            output, errors = process.communicate("release\n", timeout=10)
            self.assertEqual(process.returncode, 0, output + errors)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_two_pinned_processes_share_runtime_lock_while_upgrade_is_blocked(self):
        with self.holding_runtime_process("ensure") as first:
            with self.holding_runtime_process("run") as second:
                self.assertIsNone(first.poll())
                self.assertIsNone(second.poll())
                self.call("run", "--project", str(self.project), "--", "status")
                self.call("project-upgrade", "--project", str(self.project), "--apply", success=False)

    def test_first_pin_process_keeps_exclusive_runtime_lock(self):
        (self.project / "runtime-lock.json").unlink()
        with self.holding_runtime_process("ensure"):
            self.call("ensure", "--project", str(self.project), success=False)
            self.call("run", "--project", str(self.project), "--", "status", success=False)
        self.assertFalse((self.project / "runtime-lock.json").exists())
        self.call("ensure", "--project", str(self.project))
        self.assertEqual(self.pin()["revision"], self.first)

    def test_known_legacy_fail_does_not_deadlock_upgrade(self):
        self.newer()
        # 旧版真实任务也有规格和格式；孤立的三字段记录不是可恢复项目。
        source = self.base / "source.png"
        source.write_bytes(b"local-test-image")
        self.call("run", "--project", str(self.project), "--", "asset", "product", str(source), "--role", "product")
        record = self.project / "variants/test/run.json"
        record.parent.mkdir(parents=True)
        bootstrap.write(record.parent / "spec.json", {
            "id": "test", "provider": "dreamina", "model": "seedance2.0fast_vip", "prompt": "local fixture",
            "duration": 4, "ratio": "9:16", "resolution": "720p", "kind": "replica",
            "inputs": [{"type": "image", "asset": "product", "role": "product"}]})
        old = {"format": "video-remix-run.v1", "phase": "polling", "provider_status": "fail", "task_id": "confirmed-task"}
        bootstrap.write(record, old)
        result = self.call("project-upgrade", "--project", str(self.project), "--apply")
        self.assertTrue(result["applied"])
        self.assertEqual(bootstrap.read(record), old)
        self.assertEqual(bootstrap.read(Path(result["backup"]) / "variants/test/run.json"), old)
        for data in ({"phase": "submit_intent", "provider_status": "fail", "task_id": "x"},
                     {"phase": "polling", "provider_status": "fail"},
                     {"phase": "polling", "provider_status": "querying", "task_id": "x"}):
            self.assertTrue(bootstrap.unfinished(data))

    def test_move_project_restore_exact_version_and_offline(self):
        self.newer()
        moved = self.base / "moved"
        self.project.rename(moved)
        self.project = moved
        fresh_home = self.base / "other-machine"
        self.call("ensure", "--project", str(moved), "--offline", home=fresh_home, success=False)
        resolved = self.call("ensure", "--project", str(moved), home=fresh_home)
        self.assertEqual(resolved["revision"], self.first)
        self.call("run", "--project", str(moved), "--offline", "--", "status", home=fresh_home)

    def test_legacy_project_uses_recorded_revision(self):
        (self.project / "runtime-lock.json").unlink()
        record = self.project / "variants/test/run.json"
        record.parent.mkdir(parents=True)
        bootstrap.write(record, {"engine_revision": self.first})
        self.newer()
        selected = self.call("ensure", "--project", str(self.project))
        self.assertEqual(selected["revision"], self.first)
        self.assertEqual(self.pin()["revision"], self.first)

    def test_untrusted_lock_cannot_choose_code_source(self):
        pin = self.pin()
        pin["repo"] = "https://untrusted.invalid/code.git"
        bootstrap.write(self.project / "runtime-lock.json", pin)
        error = self.call("ensure", "--project", str(self.project), success=False)
        self.assertIn("来源不匹配", error["error"])

    def test_explicit_wrong_engine_and_direct_cli_rejected(self):
        self.newer()
        engine = self.call("update")["engine"]
        self.call("run", "--project", str(self.project), "--engine", engine, "--", "status", success=False)
        command = bootstrap.invocation(Path(engine), ["--project", str(self.project), "status"])
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("固定版本不一致", result.stderr)

    def test_corrupt_pin_restores_old_revision_not_latest(self):
        old_engine = self.call("ensure", "--project", str(self.project))["engine"]
        self.newer()
        (Path(old_engine) / "video_remix/cli.py").write_text("broken code")
        selected = self.call("ensure", "--project", str(self.project))
        self.assertEqual(selected["revision"], self.first)
        self.assertNotEqual(selected["engine"], old_engine)
        self.call("run", "--project", str(self.project), "--", "status")

    def test_backup_failure_leaves_project_pin_unchanged(self):
        self.newer()
        old_pin = self.pin()
        with patch.object(bootstrap, "backup_metadata", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                bootstrap.upgrade_project(self.project, self.home, str(self.repo), apply=True)
        self.assertEqual(self.pin(), old_pin)

    def test_legacy_conflicting_revisions_not_guessed(self):
        (self.project / "runtime-lock.json").unlink()
        second = self.newer()
        for name, sha in (("a", self.first), ("b", second)):
            record = self.project / "variants" / name / "run.json"
            record.parent.mkdir(parents=True)
            bootstrap.write(record, {"engine_revision": sha})
        self.call("ensure", "--project", str(self.project), success=False)
        self.assertFalse((self.project / "runtime-lock.json").exists())

    def test_upgrade_preview_does_not_change_default_runtime(self):
        state = bootstrap.read(self.home / "runtime.json")
        self.newer()
        self.call("project-upgrade", "--project", str(self.project))
        self.assertEqual(bootstrap.read(self.home / "runtime.json"), state)

    def test_restored_fork_binds_trusted_source_for_next_call(self):
        home = self.base / "other-home"
        self.call("ensure", "--project", str(self.project), home=home)
        info = bootstrap.project_engine(self.project, home, None, offline=True)
        self.assertEqual(info["revision"], self.first)

    def test_conflicting_project_arguments_rejected(self):
        other = self.base / "other-project"
        self.call("run", "--", "init", str(other), "--name", "Other")
        self.call("run", "--project", str(self.project), "--", "--project", str(other), "status", success=False)

    def test_installed_defaults_resume_old_project_offline_after_default_update(self):
        dest = installer.install(self.base / "installed/sct-video-remix",
                                 runtime_home=self.home, repo=str(self.repo))
        latest = self.newer()
        self.call("update")
        script = dest / "scripts/bootstrap.py"
        environment = dict(os.environ)
        environment.pop("VIDEO_REMIX_HOME", None)
        environment.pop("VIDEO_REMIX_ENGINE", None)
        result = subprocess.run([sys.executable, str(script), "ensure", "--project", str(self.project), "--offline"],
                                cwd=self.base, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["revision"], self.first)
        result = subprocess.run([sys.executable, str(script), "ensure", "--offline"],
                                cwd=self.base, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["revision"], latest)
        installer.install(dest)
        pin = self.pin()
        pin["repo"] = "https://untrusted.invalid/repo.git"
        bootstrap.write(self.project / "runtime-lock.json", pin)
        result = subprocess.run([sys.executable, str(script), "ensure", "--project", str(self.project), "--offline"],
                                cwd=self.base, env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("来源不匹配", result.stderr)

    def test_natural_installed_call_routes_existing_public_pin_to_public_runtime(self):
        dest = installer.install(self.base / "installed/sct-video-remix",
                                 runtime_home=self.home, repo=str(self.repo))
        standard = self.base / "public-runtime"
        shutil.copytree(self.home, standard)
        state = bootstrap.read(standard / "runtime.json")
        state["repo"] = bootstrap.DEFAULT_REPO
        bootstrap.write(standard / "runtime.json", state)
        pin = self.pin()
        pin["repo"] = bootstrap.DEFAULT_REPO
        bootstrap.write(self.project / "runtime-lock.json", pin)
        def expand(path):
            return standard if str(path) == "~/.local/share/video-remix" else path
        output = io.StringIO()
        with patch.object(bootstrap, "__file__", str(dest / "scripts/bootstrap.py")), \
                patch.object(Path, "expanduser", expand), \
                patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(output):
            bootstrap.main(["ensure", "--project", str(self.project), "--offline"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["revision"], self.first)
        self.assertTrue(Path(result["engine"]).is_relative_to(standard.resolve()))
        self.assertEqual(self.pin(), pin)


if __name__ == "__main__":
    unittest.main()
