"""本地组装的媒体检查与确定性硬切命令。"""

import json
import math
from pathlib import Path
import shutil
import subprocess


def media_tools():
    result = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    if not all(result.values()):
        raise ValueError("缺少 ffprobe 或 ffmpeg，请先修复本机工具")
    return result


def finite(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{label} 必须{'大于' if positive else '不小于'}零")
    return value


def duration(stream):
    try:
        return finite(float(stream["duration"]), "媒体时长", positive=True)
    except (KeyError, TypeError, ValueError):
        raise ValueError("无法确认媒体流的有限正时长") from None


def probe(path, executable):
    result = subprocess.run([executable, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError(f"媒体无法解析：{Path(path).name}")
    data = json.loads(result.stdout)
    if "filename" in data.get("format", {}):
        data["format"]["filename"] = Path(path).name
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video or not video.get("width") or not video.get("height"):
        raise ValueError("媒体缺少有效视频流")
    rotations = [s.get("rotation", 0) for s in video.get("side_data_list", [])]
    rotations.append(video.get("tags", {}).get("rotate", 0))
    if any(float(rotation) != 0 for rotation in rotations):
        raise ValueError("旋转元数据视频暂不支持，请先核实原始画幅")
    return {"video_duration": duration(video), "audio_duration": duration(audio) if audio else None,
            "audio_start": float(audio.get("start_time", 0)) if audio else None,
            "video_start": float(video.get("start_time", 0)),
            "width": video["width"], "height": video["height"],
            "sar": video.get("sample_aspect_ratio", "1:1"), "probe": data}


def audio_covers(media, start, end):
    if media["audio_duration"] is None:
        raise ValueError("所选声音来源没有音轨")
    # 不补静音、不平移有延迟的音轨；只支持与视频起点对齐的原始流。
    if not math.isfinite(media["audio_start"]) or not math.isfinite(media["video_start"]):
        raise ValueError("无法确认声画起点")
    if abs(media["audio_start"] - media["video_start"]) > 0.001:
        raise ValueError("音轨与视频起点不同，不能自动修正声音时序")
    if end > media["audio_duration"] or start >= end:
        raise ValueError("音轨不足以覆盖所选区间；不自动补声或拉长")


def command(root, contract, sources, executable, output):
    ids = list(sources)
    args = [executable, "-hide_banner", "-nostdin", "-n"]
    for source in sources.values():
        args.extend(["-i", str(root / source["raw_path"])])
    filters, video_labels, audio_labels = [], [], []
    for index, clip in enumerate(contract["clips"]):
        source = ids.index(clip["variant"])
        span = f"start={clip['in']}:end={clip['out']}"
        filters.append(f"[{source}:v:0]setpts=PTS-STARTPTS,trim={span},setpts=PTS-STARTPTS,"
                       f"fps=30[v{index}]")
        video_labels.append(f"[v{index}]")
        if contract["audio"]["mode"] == "clips":
            filters.append(f"[{source}:a:0]asetpts=PTS-STARTPTS,atrim={span},asetpts=PTS-STARTPTS,"
                           f"aresample=48000,aformat=channel_layouts=stereo[a{index}]")
            audio_labels.append(f"[a{index}]")
    filters.append("".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0[v]")
    add_audio_filter(contract, ids, audio_labels, filters)
    args.extend(["-filter_complex", ";".join(filters), "-map", "[v]", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", "-r", "30"])
    if contract["audio"]["mode"] == "silent":
        args.append("-an")
    else:
        args.extend(["-map", "[a]", "-c:a", "aac", "-ar", "48000", "-ac", "2"])
    return [*args, "-map_metadata", "-1", "-movflags", "+faststart", str(output)]


def add_audio_filter(contract, ids, labels, filters):
    audio = contract["audio"]
    if audio["mode"] == "clips":
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[a]")
    if audio["mode"] == "continuous":
        start = audio.get("in", 0)
        end = start + sum(c["out"] - c["in"] for c in contract["clips"])
        index = ids.index(audio["variant"])
        filters.append(f"[{index}:a:0]asetpts=PTS-STARTPTS,atrim=start={start}:end={end},"
                       "asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a]")
