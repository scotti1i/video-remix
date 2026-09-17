"""仅拼接已验证的生成原片；默认预览，不生成、不重试、不覆盖。"""

import hashlib
import json
import os
from pathlib import Path
import subprocess

from .assembly_media import (audio_covers, command, continuous_key, decoded_frames, finite,
                             frame_grid, media_tools, probe, probe_audio)
from .runner import disk_check, revision
from .storage import atomic, digest, locked, now, read, slug


def identifier(value):
    if not isinstance(value, str):
        raise ValueError("组装 ID 和版本 ID 必须是字符串")
    return slug(value)


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise ValueError(f"缺少必填字段：{', '.join(required)}")
    if value.keys() - set(required) - set(optional):
        raise ValueError("组装合同含不支持的字段")


def validate_contract(value):
    fields(value, ("format", "id", "clips", "audio"))
    if value["format"] not in ("video-remix-assembly.v1", "video-remix-assembly.v2"):
        raise ValueError("组装合同格式必须为 video-remix-assembly.v1 或 v2")
    identifier(value["id"])
    if not isinstance(value["clips"], list) or not value["clips"]:
        raise ValueError("clips 必须是非空列表")
    for clip in value["clips"]:
        fields(clip, ("variant", "in", "out"))
        identifier(clip["variant"])
        if finite(clip["in"], "in") >= finite(clip["out"], "out", positive=True):
            raise ValueError("每段必须满足 in < out")
    validate_audio(value)
    return value


def validate_audio(value):
    optional = ("variant", "in")
    if value["format"] == "video-remix-assembly.v2":
        optional += ("asset", "provenance")
    fields(value["audio"], ("mode",), optional)
    audio = value["audio"]
    if audio["mode"] not in ("clips", "continuous", "silent"):
        raise ValueError("audio.mode 必须为 clips / continuous / silent")
    if audio["mode"] == "continuous":
        chosen = [field for field in ("variant", "asset") if field in audio]
        if len(chosen) != 1:
            raise ValueError("continuous 必须显式选择一个 variant 或 asset，不隐式回退")
        identifier(audio[chosen[0]])
        if "asset" in audio or "provenance" in audio:
            provenance = audio.get("provenance")
            if not isinstance(provenance, str) or not 1 <= len(provenance.strip()) <= 500:
                raise ValueError("素材声音来源必须有 1–500 字的 provenance 事实说明")
        finite(audio.get("in", 0), "audio.in")
    elif set(audio) != {"mode"}:
        raise ValueError("只有 continuous 可以指定声音来源 / in / provenance")


def inside(root, relative, file=False):
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(
            item.is_symlink() for item in (path, *path.parents) if item != root and root in item.parents):
        raise ValueError("组装路径不存在或逃逸项目目录")
    if file and not path.is_file():
        raise ValueError("组装来源文件不存在")
    return path.resolve()


def source(root, variant, ffprobe):
    directory = inside(root, f"variants/{variant}")
    spec_path = inside(root, directory / "spec.json", file=True)
    run_path = inside(root, directory / "run.json", file=True)
    spec, run = read(spec_path), read(run_path)
    if not isinstance(spec, dict) or spec.get("id") != variant:
        raise ValueError("版本规格 ID 不匹配")
    if not isinstance(run, dict) or run.get("phase") != "downloaded":
        raise ValueError("只能组装已经 downloaded 的版本")
    if run.get("format") != "video-remix-run.v1":
        raise ValueError("不支持的生成运行记录格式")
    if run.get("variant") != variant or not isinstance(run.get("task_id"), str) or not run["task_id"]:
        raise ValueError("版本缺少匹配的提交任务 ID")
    spec_sha = digest(spec_path)
    if run.get("spec_sha256") != spec_sha:
        raise ValueError("运行后的 spec 已被改动或缺少摘要")
    if not isinstance(run.get("output"), str):
        raise ValueError("版本缺少原始视频路径")
    raw = inside(root, run["output"], file=True)
    if raw.resolve() != (directory / "raw.mp4").resolve():
        raise ValueError("下载原片必须属于所选版本的 raw.mp4")
    raw_sha = digest(raw)
    if run.get("output_sha256") != raw_sha:
        raise ValueError("原始视频已被修改或缺少摘要")
    return {"variant": variant, "task_id": run["task_id"], "spec": spec,
            "spec_path": str(spec_path.relative_to(root)), "spec_sha256": spec_sha,
            "raw_path": str(raw.relative_to(root)), "raw_sha256": raw_sha,
            "run_path": str(run_path.relative_to(root)),
            "media": probe(raw, ffprobe)}


def prepare(root, contract, ffprobe):
    ids = list(dict.fromkeys(c["variant"] for c in contract["clips"]))
    audio = contract["audio"]
    if audio["mode"] == "continuous" and "variant" in audio and audio["variant"] not in ids:
        ids.append(audio["variant"])
    sources = {variant: source(root, variant, ffprobe) for variant in ids}
    if audio["mode"] == "continuous" and "asset" in audio:
        sources[continuous_key(audio)] = asset_source(root, audio, ffprobe)
    return sources, validate_sources(contract, sources)


def validate_sources(contract, sources, partial=False):
    """生成前也能核验已落盘来源；缺失镜头不伪造成媒体证据。"""
    audio = contract["audio"]
    shapes = set()
    for clip in contract["clips"]:
        if partial and clip["variant"] not in sources:
            continue
        media = sources[clip["variant"]]["media"]
        shapes.add((media["width"], media["height"], media["sar"]))
        if clip["out"] > media["video_duration"]:
            raise ValueError("剪辑区间超出原始视频时长")
        if clip["out"] - clip["in"] < 1 / 30:
            raise ValueError("每段长度必须至少为一帧（1/30 秒）")
        if audio["mode"] == "clips":
            audio_covers(media, clip["in"], clip["out"])
    if len(shapes) > 1 or (not partial and not shapes):
        raise ValueError("拼接视频必须具有相同分辨率和画幅，不自动缩放或裁切")
    expected = finite(sum(c["out"] - c["in"] for c in contract["clips"]), "总时长", positive=True)
    if audio["mode"] == "continuous" and (not partial or continuous_key(audio) in sources):
        start = audio.get("in", 0)
        audio_covers(sources[continuous_key(audio)]["media"], start, start + expected)
    return expected


def asset_source(root, audio, ffprobe):
    project = read(inside(root, "project.json", file=True))
    if not isinstance(project, dict) or project.get("format") != "video-remix-project.v1":
        raise ValueError("不支持的项目素材登记格式")
    assets = project.get("assets")
    asset = assets.get(audio["asset"]) if isinstance(assets, dict) else None
    if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
        raise ValueError("声音素材必须已登记，不能回退到同名版本或镜头原声")
    path = inside(root, asset["path"], file=True)
    sha = digest(path)
    if asset.get("sha256") != sha:
        raise ValueError("声音素材已变化或缺少登记摘要")
    return {"asset": audio["asset"], "asset_path": str(path.relative_to(root)),
            "asset_sha256": sha, "registered_asset": asset, "provenance": audio["provenance"],
            "media": probe_audio(path, ffprobe)}


def contract_hash(contract):
    payload = json.dumps(contract, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def completed(root, directory, contract, sources):
    frozen = read(inside(root, directory / "contract.json", file=True))
    run = read(inside(root, directory / "run.json", file=True))
    if frozen != contract or run.get("contract_sha256") != contract_hash(contract):
        raise ValueError("同名组装合同不同，拒绝覆盖；请使用新 ID")
    if run.get("status") != "completed":
        raise ValueError("已有未完成或失败的组装，保留证据；核实后使用新 ID，不自动重试")
    if run.get("sources") != sources:
        raise ValueError("组装来源证据已变化，拒绝复用")
    output = inside(root, directory / "output.mp4", file=True)
    if run.get("output") != str(output.relative_to(root)) or run.get("output_sha256") != digest(output):
        raise ValueError("组装输出已被修改，拒绝复用")
    return {**run, "reused": True}


def assemble(root, file, execute=False):
    root = Path(root).resolve()
    contract = validate_contract(read(file))
    with locked(root / ".project.lock"):
        tools = media_tools()
        sources, expected = prepare(root, contract, tools["ffprobe"])
        directory = inside(root, f"assemblies/{contract['id']}")
        if directory.exists():
            return {**completed(root, directory, contract, sources), "execute": execute}
        grid = frame_grid(contract, sources)
        args = command(root, contract, sources, tools["ffmpeg"], directory / "partial.mp4", grid)
        audio_notes = {"clips": "保留各段原声，不能保证音色连续。",
                       "continuous": "连续声音只来自显式选择的已下载生成版本。",
                       "silent": "显式去除全部音轨。"}
        if "asset" in contract["audio"]:
            audio_notes["continuous"] = "连续声音来自显式登记素材；in 从该音轨起点计。来源说明：" + contract["audio"]["provenance"]
        preview = {"execute": execute, "contract": contract, "sources": sources,
                   "expected_duration": expected, "fps": 30, "ffmpeg_argv": args, "frame_grid": grid,
                   "note": "原速硬切，30 fps 标准重采样，不做运动插帧；" + audio_notes[contract["audio"]["mode"]]}
        if not execute:
            return preview
        disk_check(root)
        directory.mkdir(parents=True, exist_ok=False)
        atomic(directory / "contract.json", contract)
        run = {**preview, "format": contract["format"].replace("assembly.", "assembly-run."), "id": contract["id"],
               "contract_sha256": contract_hash(contract), "engine_revision": revision(),
               "status": "running", "created_at": now()}
        atomic(directory / "run.json", run)
        return render(root, directory, contract, sources, run, tools)


def render(root, directory, contract, sources, run, tools):
    try:
        process = subprocess.run(run["ffmpeg_argv"], capture_output=True, text=True, timeout=3600)
        atomic(directory / "ffmpeg.json", {"argv": run["ffmpeg_argv"], "returncode": process.returncode,
                                          "stdout": process.stdout, "stderr": process.stderr})
        if process.returncode:
            raise RuntimeError("FFmpeg 组装失败，诊断和未完成文件已保留")
        result = probe(directory / "partial.mp4", tools["ffprobe"])
        result["decoded_frames"] = decoded_frames(directory / "partial.mp4", tools["ffprobe"])
        verify_output(result, contract, sources, run["expected_duration"], run["frame_grid"])
        # 编码期间也核对原片和规格；变化时不能发布混合来源的结果。
        current, _ = prepare(root, contract, tools["ffprobe"])
        if current != sources:
            raise ValueError("组装期间来源证据变化，拒绝发布")
        output = directory / "output.mp4"
        # 同文件系统硬链接原子发布，目标已存在时失败，绝不覆盖。
        os.link(directory / "partial.mp4", output)
        (directory / "partial.mp4").unlink()
        run.update(status="completed", completed_at=now(), output=str(output.relative_to(root)),
                   output_sha256=digest(output), actual_duration=result["video_duration"],
                   actual_audio_duration=result["audio_duration"], output_probe=result["probe"])
        run["frame_grid"].update(verified=True, actual_frames=result["decoded_frames"])
        atomic(directory / "run.json", run)
        return run
    except Exception as error:
        run.update(status="failed", failed_at=now(), error_type=type(error).__name__, error=str(error))
        atomic(directory / "run.json", run)
        raise


def verify_output(result, contract, sources, expected, grid=None):
    if grid is not None:
        if result.get("decoded_frames") != grid["expected_frames"]:
            raise ValueError("实际解码帧数不符，可能源画面不足；保留未完成输出，不补尾帧")
        if abs(result["video_duration"] - grid["video_duration"]) > 0.00001:
            raise ValueError("输出时间戳不符合 30 帧栅格")
    video_tolerance = len(contract["clips"]) / 30 + 0.001
    if abs(result["video_duration"] - expected) > video_tolerance:
        raise ValueError("实际视频时长偏离合同，保留未完成输出")
    if (contract["audio"]["mode"] == "silent") != (result["audio_duration"] is None):
        raise ValueError("输出音轨与合同不符")
    if result["audio_duration"] is not None and abs(result["audio_duration"] - expected) > 1024 / 48000 + 1 / 30:
        raise ValueError("实际音轨时长偏离合同，保留未完成输出")
    media = sources[contract["clips"][0]["variant"]]["media"]
    if any(result[key] != media[key] for key in ("width", "height", "sar")):
        raise ValueError("输出画幅与原片不符")
    video = next(s for s in result["probe"]["streams"] if s["codec_type"] == "video")
    if video.get("avg_frame_rate") != "30/1":
        raise ValueError("输出帧率不是 30 fps")
