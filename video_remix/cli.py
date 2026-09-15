"""命令入口；从任意项目子目录识别项目。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from .analysis import analyze
from .compatibility import inspect
from .creative import compile_plan
from .dreamina import Dreamina
from .performance_assessment import assess_performance
from .project import add_asset, add_variant, compare, initialize, status
from .runner import attach, execute, revision
from .sound_assessment import assess
from .variation import vary
from .storage import atomic, locked, now, project_at, read, slug


def doctor(account=False):
    tools = {name: shutil.which(name) for name in ("git", "python3", "dreamina", "ffmpeg", "ffprobe", "yt-dlp")}
    result = {"version": __version__, "python": sys.version.split()[0], "tools": tools,
              "gemini_key_configured": bool(os.environ.get("GEMINI_API_KEY")),
              "gemini_model": os.environ.get("GEMINI_MODEL"),
              "video_adapters": ["dreamina"], "account_checked": account}
    if account:
        result["account"] = Dreamina().account()
    return result


def review(root, variant, decision, note):
    with locked(root / ".project.lock"):
        file = root / "variants" / slug(variant) / "run.json"
        run = read(file)
        run["review"] = {"decision": decision, "note": note, "at": now()}
        atomic(file, run)
    return run["review"]


def parser():
    p = argparse.ArgumentParser(description="视频复刻与裂变后端（macOS / Linux）")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--project", help="项目目录；省略则从当前目录向上查找")
    sub = p.add_subparsers(dest="command", required=True)
    doctor_p = sub.add_parser("doctor", help="检查本机工具和配置，不生成")
    doctor_p.add_argument("--account", action="store_true", help="只读检查即梦账号和积分")
    init = sub.add_parser("init")
    init.add_argument("path")
    init.add_argument("--name", required=True)
    asset = sub.add_parser("asset")
    asset.add_argument("id")
    asset.add_argument("file")
    asset.add_argument("--role", required=True)
    analysis = sub.add_parser("analyze", help="真实调用 Gemini 分析参考视频")
    analysis.add_argument("asset")
    analysis.add_argument("--model", default=os.environ.get("GEMINI_MODEL"))
    analysis.add_argument("--brief", required=True)
    analysis.add_argument("--refresh", action="store_true", help="显式重新调用参考分析")
    assessment = sub.add_parser("assess", help="对照参考与原片，专项声音或完整声画；不作人工验收")
    assessment.add_argument("variant")
    assessment.add_argument("--reference", required=True)
    assessment.add_argument("--focus", required=True, choices=("sound", "performance"))
    assessment.add_argument("--brief", required=True, help="具体说明核心保留项与允许变化项")
    assessment.add_argument("--model", default=os.environ.get("GEMINI_MODEL"))
    variant = sub.add_parser("variant", help="登记助手编写的版本规格 JSON")
    variant.add_argument("file")
    plan = sub.add_parser("compile", help="将制作计划原文装配并登记版本，不生成")
    plan.add_argument("file")
    variation = sub.add_parser("vary", help="按场景/人物/穿搭控制文件派生计划和差异，不生成")
    variation.add_argument("file")
    run = sub.add_parser("run", help="默认预览；--execute 才提交/恢复任务")
    run.add_argument("ids", nargs="+")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--budget", type=float, required=True)
    run.add_argument("--estimate-per-job", type=float, required=True)
    run.add_argument("--wait", type=int, default=0, help="查询等待秒数，0 只查询一次；可重复运行恢复")
    recover = sub.add_parser("attach", help="提交结果不明时，核对后绑定平台原任务")
    recover.add_argument("variant")
    recover.add_argument("task_id")
    sub.add_parser("status")
    sub.add_parser("compare")
    sub.add_parser("compatibility", help="只读检查项目格式与规格，不调用生成平台")
    rev = sub.add_parser("review")
    rev.add_argument("variant")
    rev.add_argument("decision", choices=("selected", "rejected", "pending"))
    rev.add_argument("--note", default="")
    return p


def dispatch(a):
    if a.command == "doctor":
        return doctor(a.account)
    if a.command == "init":
        return initialize(a.path, a.name)
    root = project_at(a.project)
    if a.command == "compatibility":
        return inspect(root)
    pin_file = root / "runtime-lock.json"
    if pin_file.exists() and read(pin_file).get("revision") != revision():
        raise ValueError("后端与项目固定版本不一致，请通过 Skill 启动器运行")
    handlers = {
        "asset": lambda: add_asset(root, a.id, a.file, a.role),
        "analyze": lambda: analyze(root, a.asset, a.model, a.brief, refresh=a.refresh),
        "assess": lambda: (assess_performance(root, a.variant, a.reference, a.brief, a.model)
                           if a.focus == "performance" else
                           assess(root, a.variant, a.reference, a.focus, a.brief, a.model)),
        "variant": lambda: add_variant(root, a.file),
        "compile": lambda: compile_plan(root, a.file),
        "vary": lambda: vary(root, a.file),
        "run": lambda: execute(root, a.ids, a.budget, a.estimate_per_job, a.execute, a.wait),
        "attach": lambda: attach(root, a.variant, a.task_id),
        "status": lambda: status(root), "compare": lambda: compare(root),
        "review": lambda: review(root, a.variant, a.decision, a.note),
    }
    return handlers[a.command]()


def main(argv=None):
    try:
        result = dispatch(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
