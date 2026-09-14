"""只读兼容检查；未实现的格式迁移明确拒绝，不以版本号猜测兼容。"""

from .project import asset_path, validate_spec
from .storage import read


def inspect(root):
    project = read(root / "project.json")
    if project.get("format") != "video-remix-project.v1":
        raise ValueError("不支持该项目格式；请继续使用原版本")
    for asset in project["assets"].values():
        asset_path(root, asset)
    count = 0
    for path in (root / "variants").glob("*/spec.json"):
        validate_spec(read(path), root, verify=False)
        run_path = path.parent / "run.json"
        if run_path.exists() and read(run_path).get("format") != "video-remix-run.v1":
            raise ValueError("不支持该任务记录格式；未修改项目")
        count += 1
    return {"compatible": True, "project_format": project["format"],
            "variants_checked": count, "migration_required": False}
