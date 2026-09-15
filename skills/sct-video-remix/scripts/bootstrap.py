#!/usr/bin/env python3
"""SCT Video Remix 稳定启动器；保留旧运行时路径与更新协议。仅标准库。"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

DEFAULT_REPO = "https://github.com/scotti1i/video-remix.git"


def write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".pointer-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@contextlib.contextmanager
def lock(home, filename=".update.lock"):
    import fcntl
    home.mkdir(parents=True, exist_ok=True)
    with (home / filename).open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一个进程正在检查版本，请稍后重试") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def git(*args):
    result = subprocess.run(["git", *map(str, args)], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Git 操作失败，请检查网络或仓库访问权限")
    return result.stdout.strip()


def invocation(engine, args):
    # 隔离 cwd / PYTHONPATH；启动后始终引用同一个不可变版本目录。
    code = "import sys;sys.path.insert(0,sys.argv.pop(1));from video_remix.cli import main;raise SystemExit(main())"
    return [sys.executable, "-I", "-c", code, str(engine), *args]


def healthy(engine, expected=None):
    if not engine or not (engine / "video_remix" / "cli.py").is_file():
        return False
    if not (engine / "skills/video-remix/references/workflow.md").is_file():
        return False
    try:
        if expected and git("-C", engine, "rev-parse", "HEAD") != expected:
            return False
        if git("-C", engine, "status", "--porcelain", "--untracked-files=normal"):
            return False
        result = subprocess.run(invocation(engine, ["doctor"]), capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def active(home, state):
    current = state.get("current")
    if not current:
        return None
    path = (home / "versions" / current["directory"]).resolve()
    if not path.is_relative_to((home / "versions").resolve()):
        raise RuntimeError("运行时指针路径无效")
    return path


def newest(repo):
    line = git("ls-remote", repo, "refs/heads/main")
    if not line.strip():
        raise RuntimeError("仓库没有 main 分支")
    sha = line.split()[0]
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise RuntimeError("远端未返回有效版本 SHA")
    return sha


def install_version(home, repo, sha):
    directory = f"{sha[:12]}-{uuid.uuid4().hex[:8]}"
    target = home / "versions" / directory
    target.parent.mkdir(exist_ok=True)
    # 下载到新目录，健康检查失败也不改当前指针。
    git("clone", "--quiet", "--no-checkout", "--", repo, target)
    git("-C", target, "checkout", "--quiet", "--detach", sha)
    if not healthy(target, sha):
        raise RuntimeError(f"新版本检查失败，旧版继续保留；候选目录：{target}")
    return {"directory": directory, "revision": sha}


def resolve(home, repo, offline=False, check=False, rollback=False, force=False):
    with lock(home):
        file = home / "runtime.json"
        state = read(file)
        if state.get("repo") and repo and state["repo"] != repo:
            raise RuntimeError("该运行时已绑定另一仓库；请用不同 --home，避免混用更新源")
        repo = repo or state.get("repo") or DEFAULT_REPO
        current = active(home, state)
        valid = healthy(current, state.get("current", {}).get("revision"))
        if rollback:
            return do_rollback(home, file, state)
        if offline:
            if not valid:
                raise RuntimeError("离线且没有健康的本地版本；联网运行 ensure 修复")
            return result(home, state, "offline")
        if state.get("held") and valid and not check and not force:
            return result(home, state, "held", "回退版本已固定；执行 update 恢复自动更新")
        try:
            sha = newest(repo)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            if valid:
                return result(home, state, "offline_fallback", "无法检查远端，继续使用已验证本地版")
            raise RuntimeError("无法获取远端且本地版本不可用；请检查网络与仓库地址") from None
        if check:
            return {"repo": repo, "local_healthy": valid, "current": state.get("current"),
                    "latest_revision": sha, "update_available": not valid or state["current"]["revision"] != sha}
        if not valid or state["current"]["revision"] != sha:
            next_version = install_version(home, repo, sha)
            state = {"repo": repo, "current": next_version,
                     "previous": state.get("current") if valid else state.get("previous"), "held": False}
            write(file, state)
            return result(home, state, "updated")
        if force and state.get("held"):
            state["held"] = False
            write(file, state)
        return result(home, state, "current")


def do_rollback(home, file, state):
    previous = state.get("previous")
    if not previous:
        raise RuntimeError("没有可回退版本")
    old = active(home, {"current": previous})
    if not healthy(old, previous["revision"]):
        raise RuntimeError("旧版本不健康，拒绝切换")
    state["current"], state["previous"] = previous, state["current"]
    state["held"] = True
    write(file, state)
    return result(home, state, "rolled_back", "已回退并固定旧版；执行 update 后恢复自动更新")


def result(home, state, action, warning=None):
    engine = active(home, state)
    return {"action": action, "engine": str(engine), "revision": state["current"]["revision"],
            "workflow": str(engine / "skills/video-remix/references/workflow.md"),
            "warning": warning}


def project_root(explicit=None):
    root = Path(explicit or Path.cwd()).expanduser().resolve()
    for candidate in ([root] if explicit else [root, *root.parents]):
        if (candidate / "project.json").is_file():
            return candidate
    if explicit:
        raise RuntimeError("指定目录没有 project.json")
    return None


def trusted_repo(home, repo):
    configured = read(home / "runtime.json").get("repo")
    if configured and repo and configured != repo:
        raise RuntimeError("该运行时已绑定另一仓库；请用不同 --home")
    return repo or configured or DEFAULT_REPO


def exact_version(home, repo, sha, offline=False):
    if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise RuntimeError("项目版本必须是完整 Git SHA，不能自动猜测旧版")
    with lock(home):
        state = read(home / "runtime.json")
        if state.get("repo") and state["repo"] != repo:
            raise RuntimeError("运行时已绑定另一可信仓库")
        for path in sorted((home / "versions").glob(f"{sha[:12]}-*")):
            if healthy(path, sha):
                if not state.get("repo"):
                    write(home / "runtime.json", {**state, "repo": repo})
                return {"directory": path.name, "revision": sha}
        if offline:
            raise RuntimeError("项目固定版本在本机不可用；联网恢复同一版本，不会换成最新版")
        version = install_version(home, repo, sha)
        if not state.get("repo"):
            write(home / "runtime.json", {**state, "repo": repo})
        return version


def project_engine(root, home, repo, offline=False):
    pin = read(root / "runtime-lock.json")
    repo = trusted_repo(home, repo)
    if (root / "runtime-lock.json").exists() and not pin:
        raise RuntimeError("项目版本锁为空；请恢复原锁，不自动选新版")
    if pin:
        if pin.get("format") != "video-remix-runtime.v1" or pin.get("repo") != repo:
            raise RuntimeError("项目版本锁或仓库来源不匹配；请明确指定可信 --repo 和独立 --home")
        version = exact_version(home, repo, pin.get("revision"), offline)
        return result(home, {"current": version}, "project_pinned")
    # 旧项目优先采用实际提交记录，不将历史任务悄悄切到新版。
    revisions = {read(path).get("engine_revision") for path in (root / "variants").glob("*/run.json")}
    if None in revisions:
        raise RuntimeError("旧任务没有记录后端版本，需核对后固定；不会自动选最新版")
    if len(revisions) > 1:
        raise RuntimeError("旧项目含多个运行版本，需核对后固定版本；不会自动升级")
    if revisions:
        version = exact_version(home, repo, revisions.pop(), offline)
        info = result(home, {"current": version}, "legacy_pinned")
    else:
        info = resolve(home, repo, offline)
        compatibility(Path(info["engine"]), root)
    write(root / "runtime-lock.json", {"format": "video-remix-runtime.v1",
          "repo": repo, "revision": info["revision"]})
    return info


def compatibility(engine, root):
    response = subprocess.run(invocation(engine, ["--project", str(root), "compatibility"]),
                              capture_output=True, text=True, timeout=60)
    if response.returncode:
        raise RuntimeError("目标版本未通过项目兼容检查；项目保持原样：" + response.stderr.strip())
    report = json.loads(response.stdout)
    if report.get("compatible") is not True:
        raise RuntimeError("目标版本没有确认兼容；不切换项目")
    return report


def upgrade_project(root, home, repo, apply=False, offline=False):
    if not root:
        raise RuntimeError("项目升级需要 --project 指定已有项目")
    with lock(root, ".project.lock"):
        old = read(root / "runtime-lock.json")
        if not old:
            raise RuntimeError("先用 ensure --project 固定旧项目版本，再检查升级")
        repo = trusted_repo(home, repo)
        if old.get("repo") != repo:
            raise RuntimeError("项目更新源与可信仓库不一致")
        busy = [str(p.parent.name) for p in (root / "variants").glob("*/run.json")
                if unfinished(read(p))]
        if busy:
            raise RuntimeError("仍有未完成或提交状态不明的任务，先用旧版完成：" + ", ".join(busy))
        if offline:
            target = resolve(home, repo, offline=True)
        else:
            version = exact_version(home, repo, newest(repo))
            target = result(home, {"current": version}, "upgrade_candidate")
        report = compatibility(Path(target["engine"]), root)
        output = {"current": old["revision"], "target": target["revision"],
                  "compatible": True, "applied": False, "check": report}
        if not apply or old["revision"] == target["revision"]:
            return output
        backup = backup_metadata(root)
        write(root / "runtime-lock.json", {**old, "revision": target["revision"]})
        return {**output, "applied": True, "backup": str(backup)}


def unfinished(record):
    if record.get("phase") in ("downloaded", "failed"):
        return False
    # 0.2.x 已查询到 fail，却错误留下 polling；只接受已有任务的明确终止证据。
    known_failure = str(record.get("provider_status", "")).strip().lower() in {
        "fail", "failed", "failure", "error", "cancelled", "canceled"}
    return not (record.get("phase") == "polling" and record.get("task_id") and known_failure)


def backup_metadata(root):
    import shutil
    backup = root / ".runtime-backups" / uuid.uuid4().hex
    files = [root / "project.json", root / "runtime-lock.json"]
    files += list((root / "variants").glob("*/*.json"))
    files += list((root / "analysis").glob("*/*.json"))
    for source in files:
        target = backup / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return backup


def backend_project(args):
    if "--project" in args:
        return project_root(args[args.index("--project") + 1])
    for arg in args:
        if arg.startswith("--project="):
            return project_root(arg.split("=", 1)[1])
    if args and args[0] in ("init", "doctor", "--help", "--version"):
        return None
    return project_root()


def select_engine(args, home, root):
    if args.engine:
        engine = Path(args.engine).expanduser().resolve()
        if not (engine / "video_remix/cli.py").is_file():
            raise RuntimeError("显式指定的本地工程缺少后端入口；不会覆盖开发目录")
        pin = read(root / "runtime-lock.json") if root else {}
        if pin and not healthy(engine, pin["revision"]):
            raise RuntimeError("指定后端不符合项目版本锁；省略 --engine 自动恢复固定版本")
        return {"engine": str(engine), "action": "local_development",
                "workflow": str(engine / "skills/video-remix/references/workflow.md")}
    if root:
        return project_engine(root, home, args.repo, args.offline)
    return resolve(home, args.repo, args.offline, args.action == "check",
                   args.action == "rollback", args.action == "update")


def execute_action(args, backend_args, home, root):
    if args.action == "project-upgrade":
        return upgrade_project(root, home, args.repo, args.apply, args.offline)
    info = select_engine(args, home, root)
    if args.action != "run":
        return info
    if info.get("warning"):
        print(info["warning"], file=sys.stderr)
    if args.project and "--project" not in backend_args and not any(a.startswith("--project=") for a in backend_args):
        backend_args = ["--project", str(root), *backend_args]
    environment = os.environ.copy()
    if not args.engine:
        environment["VIDEO_REMIX_RUNTIME_PIN"] = json.dumps({"format": "video-remix-runtime.v1",
            "repo": trusted_repo(home, args.repo), "revision": info["revision"]})
    else:
        environment.pop("VIDEO_REMIX_RUNTIME_PIN", None)
    return subprocess.call(invocation(Path(info["engine"]), backend_args), env=environment)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Video Remix 自动安装 / 更新启动器")
    parser.add_argument("action", choices=("ensure", "check", "update", "rollback", "run", "project-upgrade"))
    parser.add_argument("--home", default=os.environ.get("VIDEO_REMIX_HOME", "~/.local/share/video-remix"))
    parser.add_argument("--repo", help="显式指定可信 Git 仓库；默认官方 main")
    parser.add_argument("--engine", default=os.environ.get("VIDEO_REMIX_ENGINE"), help="显式开发目录，跳过托管更新")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--project", help="项目目录；ensure / run 使用该项目固定版本")
    parser.add_argument("--apply", action="store_true", help="确认执行兼容的项目升级；默认只检查")
    argv = list(sys.argv[1:] if argv is None else argv)
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    backend_args = argv[split + 1:]
    home = Path(args.home).expanduser().resolve()
    if args.project and args.action in ("check", "update", "rollback"):
        raise RuntimeError("默认后端更新不接收 --project；升级项目请用 project-upgrade")
    root = project_root(args.project) if args.project else None
    explicit_backend = "--project" in backend_args or any(a.startswith("--project=") for a in backend_args)
    if root and args.action == "run" and explicit_backend:
        other = backend_project(backend_args)
        if other and other != root:
            raise RuntimeError("启动器与后端指定了不同项目；请只指定同一个项目")
    if not root and args.action in ("ensure", "run", "project-upgrade"):
        root = backend_project(backend_args) if args.action == "run" else project_root()
    guard = lock(root, ".runtime.lock") if root else contextlib.nullcontext()
    with guard:
        info = execute_action(args, backend_args, home, root)
    if isinstance(info, int):
        return info
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
