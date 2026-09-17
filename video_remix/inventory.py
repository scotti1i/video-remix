"""只读汇总显式项目集合；机器状态与本地文件不等于人工验收。"""

from collections import Counter, defaultdict
import json
from pathlib import Path

from .dreamina import TERMINAL_FAILURES


def record(path, issues, required=False):
    if not path.exists():
        if required:
            issues.append(f"missing:{path.name}")
        return {}
    try:
        if path.is_symlink():
            raise ValueError("symlink")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not_object")
        return data
    except (OSError, ValueError, UnicodeError):
        # 不回显异常文本、坏 JSON 内容或完整响应，避免泄漏凭据。
        issues.append(f"unreadable:{path.name}")
        return {}


def scalar(data, key):
    value = data.get(key)
    return value if type(value) in (str, int, float, bool) else None


def output_info(root, directory, run, issues):
    raw = directory / "raw.mp4"
    raw_safe = not raw.is_symlink()
    output = scalar(run, "output")
    path = None
    if isinstance(output, str) and output:
        candidate = (root / output).resolve()
        if candidate.is_relative_to(root) and not (root / output).is_symlink():
            path = candidate
        else:
            issues.append("output_outside_project_or_symlink")
    exists = bool(path and path.is_file())
    if run.get("phase") == "downloaded" and not exists:
        issues.append("downloaded_without_output_file")
    if not raw_safe:
        issues.append("raw_symlink_skipped")
    return {"raw_path": str(raw), "raw_exists": raw_safe and raw.is_file(),
            "output_path": str(path) if path else None, "output_exists": exists,
            "downloaded_file_present": run.get("phase") == "downloaded" and exists}


def variant_row(root, directory, pin):
    issues = []
    spec = record(directory / "spec.json", issues, required=True)
    run_file = directory / "run.json"
    run = record(run_file, issues)
    phase = scalar(run, "phase")
    if not phase:
        phase = "unknown" if run_file.exists() or not spec else "planned"
    provider_status = str(run.get("provider_status", "")).strip().lower()
    if provider_status in TERMINAL_FAILURES and phase in (
            "submit_intent", "submitted", "polling", "download_pending"):
        issues.append("terminal_provider_failure_with_nonterminal_phase")
    if (directory / "raw.mp4").exists() and not run_file.exists():
        issues.append("raw_without_run")
    if spec.get("id") and spec["id"] != directory.name:
        issues.append("variant_id_mismatch")
    inputs = run.get("generation_inputs", run.get("inputs", spec.get("inputs", [])))
    types = [entry.get("type") for entry in inputs if isinstance(entry, dict)] if isinstance(inputs, list) else []
    row = {"project": str(root), "variant": directory.name,
           "kind": scalar(spec, "kind"), "parent": scalar(spec, "parent"),
           "model": scalar(run, "model") or scalar(spec, "model"),
           "duration": scalar(spec, "duration"), "duration_source": "spec",
           "input_types": [value for value in types if value in ("image", "video", "audio")],
           "input_source": "run" if "generation_inputs" in run or "inputs" in run else "spec",
           "engine_revision": scalar(run, "engine_revision"),
           "project_revision": scalar(pin, "revision"),
           "task_id": scalar(run, "task_id"), "phase": phase,
           "provider_status": scalar(run, "provider_status"),
           "credits": scalar(run, "credits"), "estimated_credits": scalar(run, "estimated_credits"),
           "issues": issues}
    row.update(output_info(root, directory, run, issues))
    return row


def project_rows(root):
    issues = []
    project = record(root / "project.json", issues, required=True)
    pin = record(root / "runtime-lock.json", issues)
    if project and project.get("format") != "video-remix-project.v1":
        issues.append("unsupported_project_format")
    rows = []
    variants = root / "variants"
    if variants.is_symlink():
        issues.append("variants_symlink_skipped")
    elif variants.is_dir():
        for directory in sorted(variants.iterdir()):
            if directory.is_symlink():
                issues.append(f"variant_symlink_skipped:{directory.name}")
            elif directory.is_dir():
                rows.append(variant_row(root, directory, pin))
    return {"project": str(root), "name": scalar(project, "name"),
            "issues": issues, "variant_count": len(rows)}, rows


def inventory(path):
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("--root 必须是项目或项目集合目录")
    # 只读根项目，或直接子项目；不向更深目录搜索，不跟随子目录软链。
    is_project = lambda p: (p / "project.json").exists() or (p / "variants").is_dir()
    roots = [root] if is_project(root) else [p for p in sorted(root.iterdir())
                                            if not p.is_symlink() and p.is_dir() and is_project(p)]
    projects, variants = [], []
    for project in roots:
        info, rows = project_rows(project)
        projects.append(info)
        variants.extend(rows)
    tasks = defaultdict(list)
    for row in variants:
        if row["task_id"]:
            tasks[row["task_id"]].append(row)
    duplicates = []
    for task_id, rows in tasks.items():
        if len(rows) > 1:
            duplicates.append({"task_id": task_id, "variants": [
                {"project": row["project"], "variant": row["variant"]} for row in rows]})
            for row in rows:
                row["issues"].append("duplicate_task_id")
    return {"root": str(root), "read_only": True,
            "note": "机器状态和文件存在性不代表视频有效、已交付或人工验收；重复任务不重复计单，积分逐记录列出不汇总。",
            "summary": {"projects": len(projects), "variants": len(variants),
                        "unique_tasks": len(tasks), "phases": dict(Counter(row["phase"] for row in variants)),
                        "raw_files": sum(row["raw_exists"] for row in variants),
                        "downloaded_files_present": sum(row["downloaded_file_present"] for row in variants),
                        "variants_with_issues": sum(bool(row["issues"]) for row in variants)},
            "projects": projects, "variants": variants, "duplicate_tasks": duplicates}
