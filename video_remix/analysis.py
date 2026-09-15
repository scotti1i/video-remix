"""Gemini 原生接口：保留原始返回，直接给助手使用分析文本。"""

import base64
import json
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


def analyze(root, asset_id, model, brief):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("请通过 GEMINI_API_KEY 提供密钥；不写入项目文件")
    if not model or not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        raise ValueError("用 --model 或 GEMINI_MODEL 指定当前账号支持的模型")
    base = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
    url = urllib.parse.urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.query:
        raise ValueError("GEMINI_BASE_URL 必须是无凭据、无 query 的 HTTPS 地址")
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
    body = {"contents": [{"role": "user", "parts": [{"text": prompt},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(source.read_bytes()).decode()}}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}
    directory = root / "analysis" / uuid.uuid4().hex[:12]
    directory.mkdir(parents=True)
    run = {"status": "request_intent", "model": model, "source": asset,
           "prompt": prompt, "created_at": now(), "requests": 1, "retry": 0}
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
        parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise RuntimeError("Gemini 返回空内容，检查本地原始响应")
        (directory / "analysis.md").write_text(text, encoding="utf-8")
        run.update(status="completed", usage=payload.get("usageMetadata"),
                   actual_model=payload.get("modelVersion"), finished_at=now())
        return {"analysis": str(directory / "analysis.md"), "run": str(directory / "run.json")}
    except Exception as error:
        run.update(status="failed", error_type=type(error).__name__, finished_at=now())
        raise
    finally:
        atomic(directory / "run.json", run)
