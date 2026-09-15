#!/usr/bin/env python3
"""将 Skill 安装到通用 agents 目录，可显式覆盖目的位置。"""

import argparse
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile


def installation_config(destination, runtime_home, repo):
    # 从本安装器随附的启动器复用校验，不加载目标 Skill 或项目中的代码。
    script = Path(__file__).resolve().parent / "skills/sct-video-remix/scripts/bootstrap.py"
    spec = importlib.util.spec_from_file_location("video_remix_install_bootstrap", script)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    previous = destination
    if not previous.exists() and destination.name == "sct-video-remix":
        legacy = destination.with_name("video-remix")
        if (legacy / ".video-remix-managed").is_file():
            previous = legacy
    config = bootstrap.read_install_config(previous)
    if runtime_home is not None:
        if not isinstance(runtime_home, (str, Path)) or not str(runtime_home).strip():
            raise ValueError("--runtime-home 必须是非空路径")
        config["runtime_home"] = str(Path(runtime_home).expanduser().resolve())
    if repo is not None:
        config["repo"] = repo
    if config:
        config["format"] = "video-remix-install.v1"
        bootstrap.validate_install_config(config)
    return bootstrap, config


def install(destination, runtime_home=None, repo=None):
    source = Path(__file__).resolve().parent / "skills/sct-video-remix"
    destination = Path(destination).expanduser().resolve()
    if destination.name == "video-remix":
        destination = destination.with_name("sct-video-remix")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not (destination / ".video-remix-managed").exists():
        raise ValueError(f"目标已有非本安装器管理的 Skill，不覆盖：{destination}")
    bootstrap, config = installation_config(destination, runtime_home, repo)
    temp = Path(tempfile.mkdtemp(prefix=".video-remix-", dir=destination.parent))
    shutil.copytree(source, temp, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", bootstrap.INSTALL_CONFIG))
    if config:
        bootstrap.write(temp / bootstrap.INSTALL_CONFIG, config)
    (temp / ".video-remix-managed").write_text("1\n", encoding="utf-8")
    backup = None
    if destination.exists():
        backup_root = destination.parent / ".video-remix-backups"
        backup_root.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup = backup_root / stamp
        destination.rename(backup)
    try:
        temp.rename(destination)
    except Exception:
        if backup:
            backup.rename(destination)
        raise
    retire_legacy(destination)
    return destination


def retire_legacy(destination):
    if destination.name != "sct-video-remix":
        return
    legacy = destination.with_name("video-remix")
    if not (legacy / "SKILL.md").is_file() or not (legacy / ".video-remix-managed").is_file():
        return
    # 旧 Skill 整目录可恢复；只留下启动器/指导兼容路径，不重复注册 Skill。
    alias = Path(tempfile.mkdtemp(prefix=".video-remix-alias-", dir=destination.parent))
    for folder, name in (("scripts", "bootstrap.py"), ("references", "workflow.md")):
        (alias / folder).mkdir()
        (alias / folder / name).symlink_to(Path("../../sct-video-remix") / folder / name)
    backups = destination.parent / ".video-remix-backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup = backups / (stamp + "-legacy")
    legacy.rename(backup)
    try:
        alias.rename(legacy)
    except Exception:
        backup.rename(legacy)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--destination", default="~/.agents/skills/sct-video-remix")
    p.add_argument("--runtime-home", help="为此 Skill 保存默认运行时目录；省略时保留已有配置")
    p.add_argument("--repo", help="为此 Skill 保存可信 Git 源（无密钥 URL 或绝对本地路径）")
    args = p.parse_args()
    print(install(args.destination, args.runtime_home, args.repo))
