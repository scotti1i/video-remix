#!/usr/bin/env python3
"""将 Skill 安装到通用 agents 目录，可显式覆盖目的位置。"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile


def install(destination):
    source = Path(__file__).resolve().parent / "skills/sct-video-remix"
    destination = Path(destination).expanduser().resolve()
    if destination.name == "video-remix":
        destination = destination.with_name("sct-video-remix")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not (destination / ".video-remix-managed").exists():
        raise ValueError(f"目标已有非本安装器管理的 Skill，不覆盖：{destination}")
    temp = Path(tempfile.mkdtemp(prefix=".video-remix-", dir=destination.parent))
    shutil.copytree(source, temp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
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
    args = p.parse_args()
    print(install(args.destination))
