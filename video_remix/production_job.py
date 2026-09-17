"""薄编排：已准备镜头 -> 原任务恢复 -> 完整组装，不改写创意或自动抽卡。"""

from pathlib import Path

from .assembly import (assemble, asset_source, contract_hash, fields, identifier,
                       inside, source, validate_contract, validate_sources)
from .assembly_media import audio_covers, media_tools
from .runner import execute as run_variants, positive_number, revision
from .storage import atomic, digest, locked, now, read


def load_job(root, file):
    job = read(file)
    fields(job, ("format", "id", "assembly", "budget", "estimate_per_job"))
    if job["format"] != "video-remix-production.v1":
        raise ValueError("不支持的直出任务格式")
    identifier(job["id"])
    for field in ("budget", "estimate_per_job"):
        if not positive_number(job[field]):
            raise ValueError(f"{field} 必须为有限正数")
    contract = validate_contract(job["assembly"])
    ids = list(dict.fromkeys(clip["variant"] for clip in contract["clips"]))
    audio = contract["audio"]
    if audio.get("variant") and audio["variant"] not in ids:
        ids.append(audio["variant"])
    specs = {name: read(inside(root, f"variants/{name}/spec.json", file=True)) for name in ids}
    for name, spec in specs.items():
        if spec.get("id") != name or not positive_number(spec.get("duration")):
            raise ValueError("镜头缺少匹配ID或有效时长的已登记规格")
        # runner 负责实际素材/提交状态验证；这里先排除制作时就不可能的裁切。
        inside(root, f"variants/{name}/run.json")
    for clip in contract["clips"]:
        if clip["out"] > specs[clip["variant"]]["duration"] or clip["out"] - clip["in"] < 1 / 30:
            raise ValueError("组装区间超出计划时长或不足一帧；先修制作计划")
    shapes = {(specs[c["variant"]].get("ratio"), specs[c["variant"]].get("resolution"))
              for c in contract["clips"]}
    if len(shapes) != 1:
        raise ValueError("镜头计划画幅/分辨率不一致，不先付费再修")
    duration = sum(c["out"] - c["in"] for c in contract["clips"])
    if audio.get("variant") and audio.get("in", 0) + duration > specs[audio["variant"]]["duration"]:
        raise ValueError("连续声音版本的计划时长不能覆盖全片")
    return job, ids


def snapshot(root, job, ids):
    result = {"job": job, "spec_sha256": {
        name: digest(inside(root, f"variants/{name}/spec.json", file=True)) for name in ids}}
    audio = job["assembly"]["audio"]
    if "asset" in audio:
        source = asset_source(root, audio, media_tools()["ffprobe"])
        duration = sum(c["out"] - c["in"] for c in job["assembly"]["clips"])
        audio_covers(source["media"], audio.get("in", 0), audio.get("in", 0) + duration)
        result["audio_asset"] = source
    return result


def existing_job(root, directory, frozen):
    if not directory.exists():
        return None
    saved = read(inside(root, directory / "job.json", file=True))
    state = read(inside(root, directory / "run.json", file=True))
    if saved != frozen or state.get("job_sha256") != contract_hash(frozen):
        raise ValueError("直出任务/规格/声音来源已改变；保留旧任务，使用新ID")
    return state


def check_assembly_slot(root, contract):
    directory = inside(root, f"assemblies/{contract['id']}")
    if not directory.exists():
        return
    saved = read(inside(root, directory / "contract.json", file=True))
    state = read(inside(root, directory / "run.json", file=True))
    if saved != contract or state.get("contract_sha256") != contract_hash(contract):
        raise ValueError("同名组装合同不同；生成前先使用新组装ID")
    if state.get("status") != "completed":
        raise ValueError("已有未完成或失败的组装；生成前先使用新组装ID")
    output = inside(root, directory / "output.mp4", file=True)
    if state.get("output_sha256") != digest(output):
        raise ValueError("已有组装输出变化；生成前先核实原文件")


def check_ready_sources(root, contract):
    ids = {clip["variant"] for clip in contract["clips"]}
    if "variant" in contract["audio"]:
        ids.add(contract["audio"]["variant"])
    sources = {}
    for name in ids:
        path = inside(root, f"variants/{name}/run.json")
        if path.exists() and read(path).get("phase") == "downloaded":
            raw = inside(root, f"variants/{name}/raw.mp4")
            if raw.exists():
                sources[name] = source(root, name, media_tools()["ffprobe"])
    validate_sources(contract, sources, partial=True)


def produce(root, file, execute=False, wait=0, provider=None):
    root = Path(root).resolve()
    if type(wait) is not int or not 0 <= wait <= 60:
        raise ValueError("wait 必须为0至60秒；稍后用同一命令恢复，不阻塞整夜")
    # 独立编排锁，不能持有 .project.lock 再调用内部也持锁的既有入口。
    with locked(inside(root, ".production.lock")):
        job, ids = load_job(root, file)
        check_assembly_slot(root, job["assembly"])
        check_ready_sources(root, job["assembly"])
        frozen = snapshot(root, job, ids)
        directory = inside(root, f"productions/{job['id']}")
        state = existing_job(root, directory, frozen)
        preview = run_variants(root, ids, job["budget"], job["estimate_per_job"])
        if not execute:
            return {"execute": False, "id": job["id"], "assembly": job["assembly"],
                    "generation": preview, "status": state.get("status") if state else "prepared"}
        media_tools()
        if state is None:
            state = {"format": "video-remix-production-run.v1", "id": job["id"],
                     "created_at": now(), "engine_revision": revision(), "status": "prepared",
                     "job_sha256": contract_hash(frozen), "variants": ids}
            atomic(directory / "job.json", frozen)
            atomic(directory / "run.json", state)
        return advance(root, directory, job, state, wait, provider)


def advance(root, directory, job, state, wait, provider):
    try:
        state.update(status="generating", checked_at=now())
        atomic(directory / "run.json", state)
        result = run_variants(root, state["variants"], job["budget"], job["estimate_per_job"],
                              execute=True, wait=wait, provider=provider,
                              before_submit=lambda: check_ready_sources(root, job["assembly"]))
        state.update(generation=result, checked_at=now())
        if result.get("stop"):
            state["status"] = result["stop"]
            return {**state, "run": str(directory / "run.json"), "resume": "用同一produce任务恢复；失败不自动重抽"}
        state["status"] = "assembling"
        atomic(directory / "run.json", state)
        contract = inside(root, directory / "assembly.json")
        if contract.exists() and read(contract) != job["assembly"]:
            raise ValueError("直出任务的本地组装合同已被修改")
        atomic(contract, job["assembly"])
        output = assemble(root, contract, execute=True)
        state.update(status="completed", assembly=output, output=output["output"], finished_at=now())
        return {**state, "run": str(directory / "run.json"),
                "notice": "完成表示原文件已生成并组装，不代表机器或客户验收"}
    except Exception as error:
        state.update(status="interrupted", error_type=type(error).__name__, checked_at=now())
        raise
    finally:
        atomic(directory / "run.json", state)
