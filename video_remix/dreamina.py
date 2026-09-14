"""即梦 CLI 适配器；每个方法只做一次外部操作。"""

import json
from pathlib import Path
import shutil
import subprocess
from collections import Counter

from .project import asset_path


def command(args, timeout=120):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # 平台原始错误可能带鉴权字段，不把整个 stderr 带入日志。
        raise RuntimeError(f"{Path(args[0]).name} {args[1]} 失败，退出码 {result.returncode}")
    text = result.stdout.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("CLI 未返回纯 JSON，请核对版本和登录态") from None
    if not isinstance(value, dict):
        raise RuntimeError("CLI 返回了非对象 JSON")
    return value


class Dreamina:
    def __init__(self):
        self.executable = shutil.which("dreamina")
        if not self.executable:
            raise ValueError("缺少 dreamina CLI；请安装官方 CLI 并运行 dreamina login")

    def account(self):
        data = command([self.executable, "user_credit"])
        return {"credits": data.get("total_credit"), "vip": data.get("vip_level")}

    def preflight(self, items):
        if not shutil.which("ffprobe"):
            raise ValueError("缺少 ffprobe，请安装 FFmpeg 后再生成")
        help_result = subprocess.run([self.executable, "multimodal2video", "--help"],
                                     capture_output=True, text=True, timeout=30)
        if help_result.returncode:
            raise RuntimeError("无法读取即梦 CLI 能力，请检查本地 CLI")
        for directory, spec, assets, run, _ in items:
            if run is None:
                validate_capabilities(spec, help_result.stdout)
                validate_media(spec, directory.parent.parent, assets)

    def arguments(self, spec, root, assets):
        args = [self.executable, "multimodal2video"]
        for entry in spec["inputs"]:
            args += ["--" + entry["type"], str(asset_path(root, assets[entry["asset"]]))]
        args += ["--prompt", spec["prompt"], "--duration", str(spec["duration"]),
                 "--ratio", spec["ratio"], "--video_resolution", spec["resolution"],
                 "--model_version", spec["model"], "--poll", "0"]
        return args

    def submit(self, args):
        return command(args, timeout=300)

    def query(self, task_id):
        return command([self.executable, "query_result", "--submit_id", task_id])

    def download(self, task_id, directory):
        command([self.executable, "query_result", "--submit_id", task_id,
                 "--download_dir", str(directory)], timeout=300)
        videos = list(Path(directory).rglob("*.mp4"))
        if len(videos) != 1:
            raise RuntimeError("预期一个原始 MP4，实际下载数量不符；保留目录待检查")
        return videos[0]


def unpack(payload):
    return payload.get("data", payload)


def validate_capabilities(spec, help_text):
    model = spec["model"]
    if model not in help_text or not model.startswith("seedance"):
        raise ValueError("所选模型未出现在本机 CLI 帮助中")
    is25 = model == "seedance2.5"
    if not (4 <= spec["duration"] <= (30 if is25 else 15)):
        raise ValueError("输出时长超出该模型范围")
    resolutions = {"480p", "720p"} if is25 else ({"720p", "1080p", "4k"} if model == "seedance2.0_vip" else {"720p"})
    if spec["resolution"] not in resolutions:
        raise ValueError("分辨率不受该模型支持")
    if spec["ratio"] not in {"1:1", "3:4", "16:9", "4:3", "9:16", "21:9"}:
        raise ValueError("不支持的画幅")
    counts = Counter(entry["type"] for entry in spec["inputs"])
    limits = {"image": 30, "video": 10, "audio": 10} if is25 else {"image": 9, "video": 3, "audio": 3}
    if any(counts[k] > limits[k] for k in limits) or sum(counts.values()) > (50 if is25 else 12):
        raise ValueError("输入素材数超出模型范围")
    if not is25 and not (counts["image"] + counts["video"]):
        raise ValueError("该模型至少需要一张图片或一个视频")


def validate_media(spec, root, assets):
    durations = {"video": 0, "audio": 0}
    maximum = 30 if spec["model"] == "seedance2.5" else 15
    for entry in spec["inputs"]:
        if entry["type"] == "image":
            continue
        path = asset_path(root, assets[entry["asset"]])
        result = command(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type", "-of", "json", str(path)])
        seconds = float(result["format"]["duration"])
        stream_types = {stream["codec_type"] for stream in result["streams"]}
        if entry["type"] not in stream_types or not 2 <= seconds <= maximum:
            raise ValueError("参考音视频流类型或时长不受当前模型支持")
        durations[entry["type"]] += seconds
    if any(value > maximum for value in durations.values()):
        raise ValueError("参考音视频总时长超出模型范围")
