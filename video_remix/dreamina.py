"""即梦 CLI 适配器；每个方法只做一次外部操作。"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from collections import Counter

from .generation_route import ROUTES, generation_route, validate_generation_route
from .project import asset_path


TERMINAL_FAILURES = frozenset(("fail", "failed", "failure", "error", "cancelled", "canceled"))


COMMAND_ERRORS = {
    "cli_exit": "CLI 非零退出，未识别到可安全保留的错误原因",
    "cli_timeout": "CLI 等待超时，操作结果需另行核实",
    "upload_timeout": "CLI 报告素材上传超时，任务是否提交需另行核实",
    "upload_commit_timeout": "CLI 报告素材上传确认超时，任务是否提交需另行核实",
    "compliance_confirmation_required": "平台要求先在网页端完成模型使用确认",
    "concurrency_limit": "平台并发任务数已达上限",
    "insufficient_credits": "CLI 报告账户积分不足",
    "authentication_required": "CLI 报告登录凭据无效或已过期",
    "invalid_response": "CLI 未返回预期的 JSON 对象，操作结果需另行核实",
    "missing_task_id": "提交响应未返回任务 ID，已保留不确定状态，请核对平台",
}


class CommandError(RuntimeError):
    """只携带白名单诊断，不输出命令参数、响应原文或异常上下文。"""

    def __init__(self, category, returncode=None, timeout=None, provider_code=None):
        reason = COMMAND_ERRORS[category]
        self.details = {"error_class": category, "error_reason": reason}
        for key, value in (("cli_exit_code", returncode), ("cli_timeout_seconds", timeout),
                           ("provider_error_code", provider_code)):
            if type(value) is int and abs(value) < 10 ** 10:
                self.details[key] = value
        super().__init__(reason)


def command_failure(stdout, stderr, returncode=None, timeout=None):
    # 原文只参与匹配；持久记录只包含固定分类、固定文案及有界数字。
    text = "\n".join(value.decode("utf-8", errors="replace") if isinstance(value, bytes)
                     else value for value in (stdout, stderr) if isinstance(value, (str, bytes))).lower()
    timed_out = timeout is not None or any(marker in text for marker in
                                         ("timeout", "timed out", "deadline exceeded"))
    commit = any(marker in text for marker in
                 ("commitimageupload", "commit_image_upload", "commitupload", "commit upload"))
    signatures = (("aigccomplianceconfirmationrequired", "compliance_confirmation_required"),
                  ("exceedconcurrencylimit", "concurrency_limit"),
                  ("insufficientcredits", "insufficient_credits"),
                  ("insufficient credits", "insufficient_credits"),
                  ("token expired", "authentication_required"),
                  ("invalid access token", "authentication_required"))
    category = next((category for marker, category in signatures if marker in text), "cli_exit")
    if timed_out:
        category = "upload_commit_timeout" if commit else ("upload_timeout" if "upload" in text else "cli_timeout")
    code = re.search(r"\bret=(-?\d{1,10})\b", text)
    return CommandError(category, returncode, timeout, int(code.group(1)) if code else None)


def failure_details(payload):
    # 只保存已知错误的分类和固定文案，避免平台原文夹带 token、签名 URL 或 prompt。
    reason = str(payload.get("fail_reason", ""))
    status = str(payload.get("gen_status", "")).strip().lower()
    signatures = (("exceedconcurrencylimit", "concurrency_limit", "平台并发任务数已达上限"),
                  ("generation failed", "generation_failed", "平台视频生成失败"))
    details = {"error_class": "provider_failure", "error_reason": "平台返回终止失败，未提供可安全保留的详细原因"}
    for signature, category, message in signatures:
        if signature in reason.lower():
            details = {"error_class": category, "error_reason": message}
            break
    classified = command_failure(reason, None).details
    if classified["error_class"] != "cli_exit":
        details = classified
    if status in ("cancelled", "canceled"):
        details = {"error_class": "cancelled", "error_reason": "平台任务已取消"}
    code = re.search(r"\bret=(-?\d{1,10})\b", reason)
    if code:
        details["provider_error_code"] = int(code.group(1))
    return details


def queue_snapshot(payload):
    # 保留真实排队/生成状态，过滤 debug_info 和其他可能包含内部鉴权信息的字段。
    queue = payload.get("queue_info")
    if not isinstance(queue, dict):
        return None
    statuses = {"queueing": "Queueing", "queuing": "Queuing", "generating": "Generating", "finish": "Finish"}
    result = {"queue_status": statuses.get(str(queue.get("queue_status", "")).lower(), "Unknown")}
    for key in ("queue_idx", "queue_length", "priority"):
        value = queue.get(key)
        if type(value) is int and value >= 0:
            result[key] = value
    return result


def command(args, timeout=120):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise command_failure(error.stdout, error.stderr, timeout=timeout) from None
    if result.returncode:
        raise command_failure(result.stdout, result.stderr, returncode=result.returncode)
    text = result.stdout.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise CommandError("invalid_response") from None
    if not isinstance(value, dict):
        raise CommandError("invalid_response")
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
        help_texts = {}
        for directory, spec, assets, run, _ in items:
            if run is None:
                route = generation_route(spec)
                if route not in help_texts:
                    result = subprocess.run([self.executable, ROUTES[route], "--help"],
                                            capture_output=True, text=True, timeout=30)
                    if result.returncode:
                        raise RuntimeError("无法读取即梦 CLI 能力，请检查本地 CLI")
                    help_texts[route] = result.stdout
                validate_capabilities(spec, help_texts[route])
                validate_media(spec, directory.parent.parent, assets)

    def arguments(self, spec, root, assets):
        route = validate_generation_route(spec)
        args = [self.executable, ROUTES[route]]
        for entry in spec["inputs"]:
            args += ["--" + entry["type"], str(asset_path(root, assets[entry["asset"]]))]
        args += ["--prompt", spec["prompt"], "--duration", str(spec["duration"])]
        if route == "reference":
            args += ["--ratio", spec["ratio"]]
        args += ["--video_resolution", spec["resolution"],
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
    route = validate_generation_route(spec)
    model = spec["model"]
    first_frame_models = {"seedance2.0", "seedance2.0fast", "seedance2.0_vip", "seedance2.0fast_vip"}
    if route == "first_frame" and model not in first_frame_models:
        raise ValueError("首帧路线当前仅开放 Seedance 2.0 标准/fast及VIP型号，其他型号尚未适配")
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
    if validate_generation_route(spec) == "first_frame":
        validate_first_frame(spec, root, assets)
        return
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


def validate_first_frame(spec, root, assets):
    path = asset_path(root, assets[spec["inputs"][0]["asset"]])
    result = command(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    streams = result.get("streams", [])
    image = next((s for s in streams if s.get("codec_type") == "video"), {})
    width, height = image.get("width"), image.get("height")
    formats = set(result.get("format", {}).get("format_name", "").split(","))
    image_formats = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "tiff_pipe"}
    if not formats & image_formats or len(streams) != 1 or not width or not height:
        raise ValueError("首帧必须是可解析的静态图片，不能把视频或音频标成图片")
    rotations = [s.get("rotation", 0) for s in image.get("side_data_list", [])]
    rotations.append(image.get("tags", {}).get("rotate", 0))
    if any(float(v) != 0 for v in rotations) or image.get("sample_aspect_ratio", "1:1") not in ("1:1", "N/A"):
        raise ValueError("首帧含旋转或非方形像素，先显式核实画幅，不自动旋转或裁切")
    try:
        numerator, denominator = (int(v) for v in spec["ratio"].split(":"))
    except (KeyError, ValueError, AttributeError):
        raise ValueError("首帧需要明确的计划画幅") from None
    if numerator <= 0 or denominator <= 0 or width * denominator != height * numerator:
        raise ValueError("首帧实际画幅与计划不一致；image2video从图片决定画幅，不自动裁切")
