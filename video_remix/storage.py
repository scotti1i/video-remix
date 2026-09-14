"""项目文件与互斥；持久状态不依赖聊天上下文。"""

import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slug(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise ValueError("名称限小写字母、数字和连字符，最长 64 字符")
    return value


def project_at(path=None):
    root = Path(path or Path.cwd()).expanduser().resolve()
    candidates = [root] if path else [root, *root.parents]
    for candidate in candidates:
        if (candidate / "project.json").is_file():
            data = read(candidate / "project.json")
            if data.get("format") == "video-remix-project.v1":
                return candidate
    raise ValueError("未找到 video-remix 项目；用 init 创建或 --project 指定")


@contextlib.contextmanager
def locked(path):
    # macOS / Linux：进程异常结束后由内核释放，不残留假死锁。
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有进程操作同一项目，请等待其完成") from None
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)
