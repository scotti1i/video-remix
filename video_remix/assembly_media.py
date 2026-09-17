"""本地组装的媒体检查与确定性硬切命令。"""

import json
import math
from fractions import Fraction
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


def probe_data(path, executable):
    result = subprocess.run([executable, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError(f"媒体无法解析：{Path(path).name}")
    data = json.loads(result.stdout)
    if "filename" in data.get("format", {}):
        data["format"]["filename"] = Path(path).name
    return data


def probe(path, executable):
    data = probe_data(path, executable)
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


def probe_audio(path, executable):
    data = probe_data(path, executable)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio:
        raise ValueError("所选素材没有音轨，不能回退到镜头原声")
    start = float(audio.get("start_time", 0))
    if not math.isfinite(start):
        raise ValueError("无法确认素材音轨起点")
    return {"audio_duration": duration(audio), "audio_start": start, "probe": data,
            "time_origin": "first_audio_sample"}


def continuous_key(audio):
    return f"asset:{audio['asset']}" if "asset" in audio else audio["variant"]


def frame_number(seconds):
    frames = Fraction(str(seconds)) * 30
    return (2 * frames.numerator + frames.denominator) // (2 * frames.denominator)


def frame_grid(contract, sources):
    elapsed, previous, clips = Fraction(0), 0, []
    for clip in contract["clips"]:
        elapsed += Fraction(str(clip["out"])) - Fraction(str(clip["in"]))
        boundary = frame_number(elapsed)
        count = boundary - previous
        start = frame_number(clip["in"])
        available = frame_number(sources[clip["variant"]]["media"]["video_duration"])
        if count < 1 or start + count > available:
            raise ValueError("30 帧量化后的区间没有足够源画面，不补尾帧；请调整取用区间")
        clips.append({"variant": clip["variant"], "source_start_frame": start,
                      "source_end_frame": start + count, "frames": count,
                      "source_in_seconds": start / 30, "source_out_seconds": (start + count) / 30,
                      "output_start_frame": previous, "output_end_frame": boundary})
        previous = boundary
    return {"format": "video-remix-frame-grid.v1", "fps": 30,
            "rounding": "nearest_half_up_cumulative", "expected_frames": previous,
            "requested_duration": float(elapsed), "video_duration": previous / 30,
            "audio_timing": "original_contract_seconds", "clips": clips}


def decoded_frames(path, executable):
    result = subprocess.run([executable, "-v", "error", "-select_streams", "v:0", "-count_frames",
                             "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise ValueError("输出无法完整解码计帧，拒绝发布")
    try:
        count = int(json.loads(result.stdout)["streams"][0]["nb_read_frames"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("输出缺少可验证的解码帧数") from None
    if count < 1:
        raise ValueError("输出没有解码视频帧")
    return count


def audio_covers(media, start, end):
    if media["audio_duration"] is None:
        raise ValueError("所选声音来源没有音轨")
    # 旧生成原片仍要求声画起点对齐；独立素材的区间以音轨起点计，不补静音。
    if not math.isfinite(media["audio_start"]) or not math.isfinite(media.get("video_start", media["audio_start"])):
        raise ValueError("无法确认声画起点")
    if abs(media["audio_start"] - media.get("video_start", media["audio_start"])) > 0.001:
        raise ValueError("音轨与视频起点不同，不能自动修正声音时序")
    if end > media["audio_duration"] or start >= end:
        raise ValueError("音轨不足以覆盖所选区间；不自动补声或拉长")


def command(root, contract, sources, executable, output, grid):
    ids = list(sources)
    args = [executable, "-hide_banner", "-nostdin", "-n"]
    for source in sources.values():
        args.extend(["-i", str(root / (source["asset_path"] if "asset" in source else source["raw_path"]))])
    filters, video_labels, audio_labels = [], [], []
    for index, clip in enumerate(contract["clips"]):
        source = ids.index(clip["variant"])
        span = f"start={clip['in']}:end={clip['out']}"
        frames = grid["clips"][index]
        filters.append(f"[{source}:v:0]setpts=PTS-STARTPTS,fps=30:round=near:eof_action=round,"
                       f"trim=start_frame={frames['source_start_frame']}:end_frame={frames['source_end_frame']},"
                       f"setpts=PTS-STARTPTS[v{index}]")
        video_labels.append(f"[v{index}]")
        if contract["audio"]["mode"] == "clips":
            filters.append(f"[{source}:a:0]asetpts=PTS-STARTPTS,atrim={span},asetpts=PTS-STARTPTS,"
                           f"aresample=48000,aformat=channel_layouts=stereo[a{index}]")
            audio_labels.append(f"[a{index}]")
    # concat 无法从单帧片段估算时长；已量化帧按序写回统一时间戳，不增删帧。
    filters.append("".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0,settb=1/30,setpts=N[v]")
    add_audio_filter(contract, ids, audio_labels, filters)
    args.extend(["-filter_complex", ";".join(filters), "-map", "[v]", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", "-fps_mode", "passthrough"])
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
        index = ids.index(continuous_key(audio))
        filters.append(f"[{index}:a:0]asetpts=PTS-STARTPTS,atrim=start={start}:end={end},"
                       "asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a]")
