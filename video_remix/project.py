"""素材、变体与浏览索引。"""

import html
import json
import os
from pathlib import Path
import shutil
import uuid

from .storage import atomic, digest, locked, now, read, slug


def initialize(path, name):
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with locked(root / ".project.lock"):
        if (root / "project.json").exists():
            raise ValueError("项目已存在，不覆盖")
        atomic(root / "project.json", {"format": "video-remix-project.v1",
               "name": name, "created_at": now(), "assets": {}})
        if os.environ.get("VIDEO_REMIX_RUNTIME_PIN"):
            atomic(root / "runtime-lock.json", json.loads(os.environ["VIDEO_REMIX_RUNTIME_PIN"]))
    return {"project": str(root)}


def add_asset(root, asset_id, file, role):
    slug(asset_id)
    source = Path(file).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("素材必须是文件")
    with locked(root / ".project.lock"):
        project = read(root / "project.json")
        if asset_id in project["assets"]:
            raise ValueError("素材 ID 已存在，请用新 ID 保留旧版本")
        destination = root / "assets" / f"{asset_id}-{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        asset = {"path": str(destination.relative_to(root)), "role": role,
                 "sha256": digest(destination), "bytes": destination.stat().st_size}
        project["assets"][asset_id] = asset
        atomic(root / "project.json", project)
    return {"asset_id": asset_id, **asset}


def validate_spec(spec, root, verify=True):
    slug(spec["id"])
    if spec.get("provider") != "dreamina":
        raise ValueError("当前视频适配器为 dreamina；其他平台尚未实现")
    if not isinstance(spec.get("prompt"), str) or not spec["prompt"].strip():
        raise ValueError("缺少 prompt")
    if not isinstance(spec.get("model"), str) or not spec["model"]:
        raise ValueError("缺少 model，请选择本机 CLI 支持的模型")
    if type(spec.get("duration")) is not int or spec["duration"] < 1:
        raise ValueError("duration 必须为正整数，平台能力在提交前核对")
    if not spec.get("resolution") or not spec.get("ratio"):
        raise ValueError("缺少 resolution / ratio")
    if spec.get("kind") not in ("replica", "variation", "rerun"):
        raise ValueError("kind 必须是 replica / variation / rerun")
    if spec["kind"] != "replica" and not spec.get("parent"):
        raise ValueError("裂变或重生成必须指定 parent")
    if spec["kind"] == "variation" and not spec.get("change"):
        raise ValueError("裂变必须记录 change")
    if spec.get("parent"):
        parent = slug(spec["parent"])
        if parent == spec["id"] or not (root / "variants" / parent / "spec.json").is_file():
            raise ValueError("parent 必须指向已存在的另一版本")
    assets = read(root / "project.json")["assets"]
    inputs = spec.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("inputs 必须是有序素材列表")
    for entry in inputs:
        if entry.get("type") not in ("image", "video", "audio"):
            raise ValueError("输入 type 必须是 image/video/audio")
        if entry.get("asset") not in assets or not entry.get("role"):
            raise ValueError("输入需要已登记的 asset 和清晰 role")
        asset = assets[entry["asset"]]
        path = asset_path(root, asset)
        if verify and digest(path) != asset["sha256"]:
            raise ValueError(f"素材已变化：{entry['asset']}")
    return assets


def asset_path(root, asset):
    path = (root / asset["path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("素材不存在或逃逸项目目录")
    return path


def add_variant(root, file):
    spec = read(file)
    with locked(root / ".project.lock"):
        validate_spec(spec, root)
        validate_rerun(spec, root)
        target = root / "variants" / spec["id"] / "spec.json"
        if target.exists():
            raise ValueError("版本已存在，请新增版本 ID")
        atomic(target, spec)
    return {"variant": spec["id"], "spec": str(target)}


def validate_rerun(spec, root):
    # 只校验新登记，历史规格与任务按原记录恢复。
    if spec["kind"] != "rerun":
        return
    parent = read(root / "variants" / spec["parent"] / "spec.json")
    fields = ("provider", "model", "duration", "ratio", "resolution", "inputs", "prompt")
    changed = [field for field in fields if spec.get(field) != parent.get(field)]
    if changed:
        raise ValueError("rerun 必须保持父版生成条件；有改动请用 variation：" + ", ".join(changed))


def status(root):
    variants = []
    for path in sorted((root / "variants").glob("*/spec.json")):
        spec = read(path)
        run = read(path.parent / "run.json") if (path.parent / "run.json").exists() else {}
        variants.append({"id": spec["id"], "kind": spec["kind"], "change": spec.get("change", ""),
                         "phase": run.get("phase", "prepared"), "task_id": run.get("task_id"),
                         "credits": run.get("credits"), "output": run.get("output"),
                         "provider_status": run.get("provider_status"),
                         "queue_info": run.get("queue_info"),
                         "error_class": run.get("error_class"),
                         "error_reason": run.get("error_reason"),
                         "review": run.get("review", {})})
    return {"project": str(root), "variants": variants}


def compare(root):
    # 可直接打开的静态结果表，不依赖飞书或额外服务。
    rows = []
    for item in status(root)["variants"]:
        spec = read(root / "variants" / item["id"] / "spec.json")
        conditions = html.escape("; ".join(f"{e['type']}: {e['role']}" for e in spec["inputs"]))
        output = item.get("output")
        video = f'<video controls preload="metadata" width="240" src="{html.escape(output, quote=True)}"></video>' if output else html.escape(item["phase"])
        detail = html.escape(item["change"] or item["kind"])
        rows.append(f'<tr><td>{html.escape(item["id"])}<br>{detail}<br>{conditions}<br>{html.escape(spec["model"])} · {spec["duration"]}s<br>积分：{item["credits"]}<br>{html.escape(str(item["review"]))}</td><td>{video}</td></tr>')
    page = '<!doctype html><meta charset="utf-8"><title>视频版本对比</title><table border="1" cellpadding="12"><tr><th>条件</th><th>原始结果</th></tr>' + "".join(rows) + "</table>"
    target = root / "compare.html"
    target.write_text(page, encoding="utf-8")
    return {"report": str(target)}
