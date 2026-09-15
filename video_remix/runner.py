"""按版本持久运行：提交一次、查同一 ID、原文件下载。"""

import math
from pathlib import Path
import shutil
import subprocess
import time

from . import __version__
from .dreamina import Dreamina, TERMINAL_FAILURES, failure_details, queue_snapshot, unpack
from .project import validate_spec
from .storage import atomic, digest, locked, now, read, slug


def revision():
    result = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent),
                             "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else __version__


def positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def disk_check(root):
    free = shutil.disk_usage(root).free
    if free < 50 * 1024 ** 3:
        raise ValueError("可用磁盘不足 50 GiB，暂停媒体操作")


def prepare(root, ids):
    items = []
    for variant_id in ids:
        directory = root / "variants" / slug(variant_id)
        spec = read(directory / "spec.json")
        run = read(directory / "run.json") if (directory / "run.json").exists() else None
        if run and run["spec_sha256"] != digest(directory / "spec.json"):
            raise ValueError("运行后的 spec 已被改动；请创建新版本")
        if run and run["phase"] == "downloaded":
            output = (root / run["output"]).resolve()
            if not output.is_relative_to(root.resolve()):
                raise ValueError("结果路径逃逸项目目录")
            if not output.exists():
                run["phase"] = "download_pending"
            elif digest(output) != run["output_sha256"]:
                raise ValueError("原始视频已被修改，请保留改动并另行核实")
        items.append((directory, spec, {}, run, []))
    if len(set(ids)) != len(ids):
        raise ValueError("同一次请求不能重复包含相同版本")
    return items


def execute(root, ids, budget, estimate, execute=False, wait=0, provider=None):
    if not positive_number(budget) or not positive_number(estimate):
        raise ValueError("budget 和 estimate-per-job 必须是有限正数")
    with locked(root / ".project.lock"):
        items = prepare(root, ids)
        summary = [{"id": spec["id"], "model": spec["model"], "duration": spec["duration"],
                    "inputs": spec["inputs"], "prompt": spec["prompt"],
                    "existing_task": run.get("task_id") if run else None} for _, spec, _, run, _ in items]
        if not execute:
            for _, spec, _, run, _ in items:
                if run is None:
                    validate_spec(spec, root)
            return {"execute": False, "budget": budget, "estimated_per_new_job": estimate,
                    "note": "预估不是平台硬扣费上限，超出预估会停止后续提交", "variants": summary}
        return execute_items(root, items, provider, budget, estimate, wait)


def prepare_submissions(root, items, provider):
    # 真正准备提交时再核验全部新规格；查询原任务不依赖旧素材或生成接口。
    disk_check(root)
    pending = []
    for directory, spec, _, run, _ in items:
        if run is None:
            assets = validate_spec(spec, root)
            pending.append((directory, spec, assets, None, provider.arguments(spec, root, assets)))
    provider.preflight(pending)
    account = provider.account()
    credit = account.get("credits")
    if type(credit) not in (int, float) or not math.isfinite(credit):
        raise ValueError("无法确认账户余额")
    return {spec["id"]: (assets, args) for _, spec, assets, _, args in pending}, credit


def execute_items(root, items, provider, budget, estimate, wait):
    existing = sum(record.get("credits") if type(record.get("credits")) in (int, float)
                   else record.get("estimated_credits", estimate)
                   for _, _, _, record, _ in items if record)
    spent, results, submissions = 0, [], None
    for directory, spec, assets, run, args in items:
        if run and run["phase"] == "submit_intent":
            raise ValueError("上次提交结果不明；核对平台后用 attach 绑定原任务 ID，禁止重交")
        if run and str(run.get("provider_status", "")).strip().lower() in TERMINAL_FAILURES:
            # 兼容旧记录把 fail 留成 polling 的情况；已知终止的任务不再空查。
            if run["phase"] != "failed":
                run.update(phase="failed", **failure_details({"gen_status": run["provider_status"]}))
                atomic(directory / "run.json", run)
        if run is None:
            provider = provider or Dreamina()
            if submissions is None:
                submissions, credit = prepare_submissions(root, items, provider)
            assets, args = submissions[spec["id"]]
            if existing + spent + estimate > budget or spent + estimate > credit:
                return {"stop": "budget", "new_credits": spent, "results": results}
            run = submit_once(directory, spec, assets, args, provider, estimate)
            actual = run.get("credits")
            if type(actual) not in (int, float) or not math.isfinite(actual) or actual < 0:
                return {"stop": "unknown_price", "results": results + [run]}
            spent += actual
            if actual > estimate or existing + spent > budget:
                return {"stop": "price_exceeded_estimate", "new_credits": spent, "results": results + [run]}
        if run["phase"] not in ("downloaded", "failed"):
            provider = provider or Dreamina()
            run = poll(directory, run, provider, wait)
        results.append(run)
        if run["phase"] != "downloaded":
            # wait 结束只代表本次等待结束；前一单未成功落盘时不提交下一单。
            return {"stop": "failed" if run["phase"] == "failed" else "pending",
                    "new_credits": spent, "results": results}
    return {"new_credits": spent, "results": results}


def submit_once(directory, spec, assets, args, provider, estimate):
    run = {"format": "video-remix-run.v1", "variant": spec["id"], "created_at": now(),
           "engine_revision": revision(), "spec_sha256": digest(directory / "spec.json"),
           "phase": "submit_intent", "submit_attempts": 1, "task_id": None,
           "estimated_credits": estimate, "credits": None, "provider": spec["provider"],
           "model": spec["model"], "prompt": spec["prompt"], "actual_argv": args,
           "inputs": [{**entry, **assets[entry["asset"]]} for entry in spec["inputs"]]}
    atomic(directory / "run.json", run)
    # 状态先落盘。超时、解析失败或进程中断都保留 submit_intent。
    try:
        payload = unpack(provider.submit(args))
    except Exception as error:
        run.update(error_type=type(error).__name__, failed_at=now())
        atomic(directory / "run.json", run)
        raise
    task_id = payload.get("submit_id")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError("提交未返回任务 ID，已保留不确定状态，请核对平台")
    run.update(phase="submitted", task_id=task_id, credits=payload.get("credit_count"))
    atomic(directory / "run.json", run)
    return run


def poll(directory, run, provider, wait):
    deadline = time.monotonic() + max(0, wait)
    while True:
        payload = unpack(provider.query(run["task_id"]))
        status = str(payload.get("gen_status", "unknown")).strip().lower()
        run.update(provider_status=status, checked_at=now(), queue_info=queue_snapshot(payload))
        if payload.get("credit_count") is not None:
            run["credits"] = payload["credit_count"]
        if status in TERMINAL_FAILURES:
            run.update(phase="failed", **failure_details(payload))
        else:
            run["phase"] = "download_pending" if status == "success" else "polling"
        atomic(directory / "run.json", run)
        if status == "success":
            return download(directory, run, provider)
        if run["phase"] == "failed" or time.monotonic() >= deadline:
            return run
        time.sleep(min(10, max(0, deadline - time.monotonic())))


def download(directory, run, provider):
    disk_check(directory)
    if not shutil.which("ffprobe"):
        raise ValueError("缺少 ffprobe，请安装 FFmpeg 后再下载验证原片")
    temp = directory / "download"
    temp.mkdir(exist_ok=True)
    file = provider.download(run["task_id"], temp)
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(file)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode or "video" not in result.stdout:
        raise RuntimeError("下载文件不是可解析的视频，保留原文件待检查")
    output = directory / "raw.mp4"
    file.replace(output)
    root = directory.parent.parent
    run.update(phase="downloaded", output=str(output.relative_to(root)),
               output_sha256=digest(output), output_bytes=output.stat().st_size,
               completed_at=now(), postprocessing=False)
    atomic(directory / "run.json", run)
    return run


def attach(root, variant, task_id, provider=None):
    # 仅绑定可验证存在的任务，不发起新生成。
    provider = provider or Dreamina()
    with locked(root / ".project.lock"):
        directory = root / "variants" / slug(variant)
        run = read(directory / "run.json")
        if run["phase"] != "submit_intent":
            raise ValueError("attach 只处理结果不明的提交")
        payload = unpack(provider.query(task_id))
        if payload.get("submit_id") != task_id or payload.get("prompt") != run["prompt"]:
            raise ValueError("平台任务 ID / prompt 不匹配，不绑定")
        run.update(task_id=task_id, phase="submitted", credits=payload.get("credit_count"), recovered_at=now())
        atomic(directory / "run.json", run)
    return run
