"""Gemini 原生接口：保留原始返回，直接给助手使用分析文本。"""

import base64
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .project import asset_path
from .storage import atomic, digest, now, read


class IncompleteResponse(RuntimeError):
    """响应证据已保存，但不能作为完整分析使用；调用方不得自动重试。"""


def response_completion(payload):
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return {"finish_reason": "NO_CANDIDATE", "completion_verified": False,
                "completion_note": "没有有效候选，响应不完整"}, False
    reason = candidates[0].get("finishReason")
    if reason is None or reason == "":
        return {"finish_reason": None, "completion_verified": False,
                "completion_note": "网关未提供结束原因；沿用有文本即完成的兼容行为，完整性未验证"}, True
    # 不把未知错误字段原文带入日志/异常；原始证据仅留 response.raw.json。
    known = {"STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "BLOCKLIST",
             "PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL", "IMAGE_SAFETY",
             "IMAGE_PROHIBITED_CONTENT", "IMAGE_OTHER", "NO_IMAGE", "UNEXPECTED_TOOL_CALL",
             "TOO_MANY_TOOL_CALLS", "MISSING_THOUGHT_SIGNATURE", "FINISH_REASON_UNSPECIFIED"}
    safe_reason = reason if isinstance(reason, str) and reason in known else "UNKNOWN_NON_STOP"
    stopped = safe_reason == "STOP"
    return {"finish_reason": safe_reason, "completion_verified": stopped,
            "completion_note": "服务端正常结束" if stopped else "服务端未正常结束，内容可能截断或被阻断"}, stopped


def configuration(model):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("请通过 GEMINI_API_KEY 提供密钥；不写入项目文件")
    if not model or not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        raise ValueError("用 --model 或 GEMINI_MODEL 指定当前账号支持的模型")
    base = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
    url = urllib.parse.urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.query or url.fragment:
        raise ValueError("GEMINI_BASE_URL 必须是无凭据、无 query 的 HTTPS 地址")
    if os.environ.get("GEMINI_AUTH_MODE", "google") not in ("google", "bearer"):
        raise ValueError("GEMINI_AUTH_MODE 必须是 google 或 bearer")
    return key, base


def cached_analysis(root, signature):
    incomplete = None
    for path in sorted((root / "analysis").glob("*/run.json"), reverse=True):
        try:
            run = read(path)
            text = path.parent / "analysis.md"
            raw = path.parent / "response.raw.json"
            if run.get("request_signature") != signature or not raw.is_file():
                continue
            completion, acceptable = response_completion(read(raw))
            if not acceptable or run.get("status") == "incomplete":
                incomplete = path
                continue
            if run.get("status") != "completed" or not text.is_file() or not text.read_text().strip():
                continue
            result = {"analysis": str(text), "run": str(path), "cache_hit": True, **completion}
            correction = path.parent / "corrections.md"
            if correction.is_file():
                result["corrections"] = str(correction)
            return result
        except (OSError, ValueError, TypeError):
            continue
    if incomplete:
        raise IncompleteResponse(f"已有同条件不完整响应：{incomplete}；未复用、未重新请求。核对证据并获准后显式 --refresh")
    return None


def sampling_options(fps):
    if fps is None:
        return {}
    if type(fps) not in (int, float) or not math.isfinite(fps) or not 0 < fps <= 24:
        raise ValueError("--video-fps 必须是大于0、不超过24的有限数字")
    return {"videoMetadata": {"fps": float(fps)}}


def analyze(root, asset_id, model, brief, refresh=False, video_fps=None):
    sampling = sampling_options(video_fps)
    key, base = configuration(model)
    asset = read(root / "project.json")["assets"][asset_id]
    source = asset_path(root, asset)
    if digest(source) != asset["sha256"]:
        raise ValueError("参考文件已变化，请重新登记")
    if source.stat().st_size > 40 * 1024 ** 2:
        raise ValueError("内联分析入口限制 40 MiB，请先截取目标视频区间")
    prompt = (Path(__file__).parent / "reference-analysis.txt").read_text(encoding="utf-8")
    prompt += "\n用户本次需求：\n" + brief
    mime = mimetypes.guess_type(source)[0]
    if not mime or not mime.startswith("video/"):
        raise ValueError("参考分析需要视频文件")
    identity = [asset["sha256"], model, base, prompt]
    if sampling:
        identity.append(sampling)
    signature = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    cached = None if refresh else cached_analysis(root, signature)
    if cached:
        return cached
    body = {"contents": [{"role": "user", "parts": [{"text": prompt},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(source.read_bytes()).decode()},
             **sampling}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}
    directory = root / "analysis" / uuid.uuid4().hex[:12]
    directory.mkdir(parents=True)
    run = {"status": "request_intent", "model": model, "source": asset,
           "prompt": prompt, "created_at": now(), "requests": 1, "retry": 0,
           "request_signature": signature, "refresh": refresh,
           "requested_video_fps": video_fps}
    atomic(directory / "run.json", run)
    return request(directory, base, model, key, body, run)


def request(directory, base, model, key, body, run):
    endpoint = f"{base}/v1beta/models/{model}:generateContent"
    mode = os.environ.get("GEMINI_AUTH_MODE", "google")
    if mode not in ("google", "bearer"):
        raise ValueError("GEMINI_AUTH_MODE 必须是 google 或 bearer")
    headers = {"Content-Type": "application/json"}
    headers.update({"Authorization": f"Bearer {key}"} if mode == "bearer"
                   else {"x-goog-api-key": key})
    run["auth_mode"] = mode
    req = urllib.request.Request(endpoint, json.dumps(body).encode(),
                                 headers)
    try:
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                raw, code = response.read(), response.status
        except urllib.error.HTTPError as error:
            with error:
                raw, code = error.read(), error.code
        # 先保留原文；API 失败与 JSON 解析失败也有可读证据。
        (directory / "response.raw.json").write_bytes(raw)
        run["http_status"] = code
        if code != 200:
            raise RuntimeError(f"Gemini HTTP {code}；原始响应保存在本地 analysis 目录")
        payload = json.loads(raw)
        completion, acceptable = response_completion(payload)
        run.update(**completion, usage=payload.get("usageMetadata") if isinstance(payload, dict) else None,
                   actual_model=payload.get("modelVersion") if isinstance(payload, dict) else None)
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
        content = candidate.get("content")
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = "\n".join(p["text"] for p in parts if isinstance(p, dict)
                         and isinstance(p.get("text"), str) and not p.get("thought")) if isinstance(parts, list) else ""
        if text.strip():
            (directory / "analysis.md").write_text(text, encoding="utf-8")
        if not acceptable:
            run.update(status="incomplete", finished_at=now())
            raise IncompleteResponse("Gemini 响应未完整结束；原始响应、部分文本和用量已保留，未自动重试")
        if not text.strip():
            raise RuntimeError("Gemini 返回空内容，检查本地原始响应")
        run.update(status="completed", finished_at=now())
        return {"analysis": str(directory / "analysis.md"), "run": str(directory / "run.json"), **completion}
    except Exception as error:
        run.update(status="incomplete" if run.get("status") == "incomplete" else "failed",
                   error_type=type(error).__name__, finished_at=now())
        raise
    finally:
        atomic(directory / "run.json", run)
