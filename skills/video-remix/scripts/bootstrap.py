#!/usr/bin/env python3
"""稳定启动器：版本目录不可变，每次调用解析一次运行时。仅标准库。"""

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
def lock(home):
    import fcntl
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".update.lock").open("a") as stream:
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


def resolve(home, repo, offline=False, check=False, rollback=False):
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
                     "previous": state.get("current") if valid else state.get("previous")}
            write(file, state)
            return result(home, state, "updated")
        return result(home, state, "current")


def do_rollback(home, file, state):
    previous = state.get("previous")
    if not previous:
        raise RuntimeError("没有可回退版本")
    old = active(home, {"current": previous})
    if not healthy(old, previous["revision"]):
        raise RuntimeError("旧版本不健康，拒绝切换")
    state["current"], state["previous"] = previous, state["current"]
    write(file, state)
    return result(home, state, "rolled_back", "本次已回退；用 run --offline 保持旧版，联网 ensure 会重新跟随 main")


def result(home, state, action, warning=None):
    engine = active(home, state)
    return {"action": action, "engine": str(engine), "revision": state["current"]["revision"],
            "workflow": str(engine / "skills/video-remix/references/workflow.md"),
            "warning": warning}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Video Remix 自动安装 / 更新启动器")
    parser.add_argument("action", choices=("ensure", "check", "update", "rollback", "run"))
    parser.add_argument("--home", default=os.environ.get("VIDEO_REMIX_HOME", "~/.local/share/video-remix"))
    parser.add_argument("--repo", help="显式指定可信 Git 仓库；默认官方 main")
    parser.add_argument("--engine", default=os.environ.get("VIDEO_REMIX_ENGINE"), help="显式开发目录，跳过托管更新")
    parser.add_argument("--offline", action="store_true")
    argv = list(sys.argv[1:] if argv is None else argv)
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    backend_args = argv[split + 1:]
    if args.engine:
        engine = Path(args.engine).expanduser().resolve()
        if not (engine / "video_remix/cli.py").is_file():
            raise RuntimeError("显式指定的本地工程缺少后端入口；不会覆盖开发目录")
        info = {"engine": str(engine), "action": "local_development",
                "workflow": str(engine / "skills/video-remix/references/workflow.md")}
    else:
        info = resolve(Path(args.home).expanduser().resolve(), args.repo, args.offline,
                       args.action == "check", args.action == "rollback")
    if args.action == "run":
        if info.get("warning"):
            print(info["warning"], file=sys.stderr)
        return subprocess.call(invocation(Path(info["engine"]), backend_args))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
