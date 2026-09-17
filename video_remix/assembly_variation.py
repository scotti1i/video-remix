"""从父剪辑清单派生逐镜裂变；不生成视频、不改变音轨或剪辑时间。"""

from copy import deepcopy
from pathlib import Path

from .assembly import fields, inside, validate_contract
from .creative import assemble as compile_spec, store_plan
from .project import validate_rerun, validate_spec
from .storage import atomic, digest, locked, now, read, slug
from .variation import prepare_variation


NOTE = ("仅冻结合同顺序、剪辑区间、音轨安排及未修改的计划字段；"
        "不保证跨镜身份、核心效果或新素材无语义冲突。clips 模式保留各镜自带声音，"
        "替换镜头也会替换该镜原声；continuous 的音轨来源不变。未提交生成或执行组装。")
RECOVERY = ("保留已登记子镜头和本记录。核对 plans/variants 后，用新批次 ID；"
            "已完整登记的子镜头用 variant 引用，未完整登记的子镜头改用新 ID。不自动重试或覆盖。")


def parent_contract(root, file, parent):
    if not isinstance(parent, str) or not parent.strip():
        raise ValueError("parent 必须为明确的父组装合同路径")
    path = Path(parent).expanduser()
    path = (file.parent / path).resolve() if not path.is_absolute() else path.resolve()
    contract = validate_contract(read(path))
    frozen = inside(root, f"assemblies/{contract['id']}/contract.json")
    if frozen.exists() and read(frozen) != contract:
        raise ValueError("父合同与已有组装冻结合同不同，先核实来源")
    return path, contract


def parent_specs(root, contract):
    result = {}
    for clip in contract["clips"]:
        variant = clip["variant"]
        file = inside(root, f"variants/{variant}/spec.json", file=True)
        spec = read(file)
        if spec.get("id") != variant:
            raise ValueError("父镜头规格 ID 不匹配")
        validate_spec(spec, root)
        run = inside(root, f"variants/{variant}/run.json")
        if run.exists() and read(run).get("spec_sha256") != digest(file):
            raise ValueError("父镜头运行后规格被修改")
        if clip["out"] > spec["duration"]:
            raise ValueError("父合同取用区间超出镜头计划时长")
        result[variant] = {"spec": spec, "sha256": digest(file)}
    return result


def check_child(base, child, end):
    if child["parent"] != base["id"]:
        raise ValueError("替换镜头的 parent 必须与该 clip 的父 variant 相同")
    for field in ("provider", "model", "duration", "ratio", "resolution", "creative_mode"):
        if child.get(field) != base.get(field):
            raise ValueError(f"整片窄裂变不能改变镜头 {field}")
    if end > child["duration"]:
        raise ValueError("新镜头时长不能覆盖既有取用区间")


def registered_replacement(root, variant, base, end):
    slug(variant)
    file = inside(root, f"variants/{variant}/spec.json", file=True)
    spec = read(file)
    if spec.get("id") != variant:
        raise ValueError("替换镜头规格 ID 不匹配")
    validate_spec(spec, root)
    check_child(base, spec, end)
    run = inside(root, f"variants/{variant}/run.json")
    if run.exists() and read(run).get("spec_sha256") != digest(file):
        raise ValueError("替换版本运行后规格被修改")
    return {"variant": variant, "spec_sha256": digest(file), "registered": True}


def prepare_change(root, change, clip, base):
    fields(change, ("clip",), ("control", "variant"))
    if ("control" in change) == ("variant" in change):
        raise ValueError("每镜恰选 control 或已登记 variant")
    if "variant" in change:
        return registered_replacement(root, change["variant"], base, clip["out"])
    plan, report = prepare_variation(root, change["control"])
    spec = compile_spec(plan)
    validate_spec(spec, root)
    validate_rerun(spec, root)
    check_child(base, spec, clip["out"])
    for folder in ("plans", "variants"):
        if inside(root, f"{folder}/{plan['id']}").exists():
            raise ValueError("新镜头 ID 已存在，不能覆盖；已有返修请显式用 variant")
    return {"variant": plan["id"], "plan": plan, "spec": spec,
            "control": change["control"], "diff": report, "registered": False}


def preview(root, file):
    control = read(file)
    fields(control, ("format", "id", "parent", "changes"))
    if control["format"] != "video-remix-assembly-variation.v1":
        raise ValueError("不支持的整片裂变控制格式")
    slug(control["id"])
    path, parent = parent_contract(root, file, control["parent"])
    if control["id"] == parent["id"]:
        raise ValueError("整片裂变必须使用新组装 ID")
    directory = inside(root, f"assembly-variations/{control['id']}")
    if directory.exists() or inside(root, f"assemblies/{control['id']}").exists():
        raise ValueError("整片裂变或组装 ID 已存在，保留证据，不覆盖或自动重试")
    if not isinstance(control["changes"], list) or not control["changes"]:
        raise ValueError("changes 至少指定一镜，其余镜头沿用父版")
    sources = parent_specs(root, parent)
    child, prepared, used, variants = deepcopy(parent), [], set(), set()
    child["id"] = control["id"]
    for change in control["changes"]:
        number = change.get("clip") if isinstance(change, dict) else None
        if type(number) is not int or not 1 <= number <= len(parent["clips"]) or number in used:
            raise ValueError("clip 是不重复的 1 起始镜头序号")
        clip = parent["clips"][number - 1]
        item = prepare_change(root, change, clip, sources[clip["variant"]]["spec"])
        if not item["registered"] and item["variant"] in variants:
            raise ValueError("新镜头 ID 不能重复登记")
        variants.add(item["variant"])
        used.add(number)
        child["clips"][number - 1]["variant"] = item["variant"]
        prepared.append({"clip": number, "parent": clip["variant"], **item})
    return {"control": control, "parent_path": str(path), "parent_sha256": digest(path),
            "parent_contract": parent, "parent_specs": sources, "contract": child,
            "prepared": prepared, "note": NOTE}


def vary_assembly(root, file, execute=False):
    root, file = Path(root).resolve(), Path(file).expanduser().resolve()
    if not execute:
        return {**preview(root, file), "execute": False}
    # 批次锁只保护此入口；单镜登记继续使用原保存入口与项目锁。
    with locked(root / ".assembly-variation.lock"):
        result = preview(root, file)
        directory = inside(root, f"assembly-variations/{result['control']['id']}")
        directory.mkdir(parents=True, exist_ok=False)
        atomic(directory / "control.json", result["control"])
        atomic(directory / "parent.json", result["parent_contract"])
        run = {**result, "execute": True, "status": "preparing", "created_at": now(),
               "registered_variants": [], "recovery": RECOVERY}
        atomic(directory / "run.json", run)
        return register(root, directory, result, run)


def register(root, directory, result, run):
    try:
        for item in result["prepared"]:
            if not item["registered"]:
                store_plan(root, item["plan"], {"variation.json": item["control"], "diff.json": item["diff"]})
                run["registered_variants"].append(item["variant"])
                atomic(directory / "run.json", run)
        if digest(result["parent_path"]) != result["parent_sha256"]:
            raise ValueError("登记期间父合同变化，拒绝发布新组装合同")
        if parent_specs(root, result["parent_contract"]) != result["parent_specs"]:
            raise ValueError("登记期间父镜头规格变化，拒绝发布新组装合同")
        atomic(directory / "assembly.json", result["contract"])
        run.update(status="prepared", assembly=str(directory / "assembly.json"), completed_at=now())
        atomic(directory / "run.json", run)
        return run
    except Exception as error:
        run.update(status="failed", error=str(error), failed_at=now())
        atomic(directory / "run.json", run)
        raise
