"""只读兼容检查；未实现的格式迁移明确拒绝，不以版本号猜测兼容。"""

from pathlib import Path

from .assembly import contract_hash, fields, inside, validate_contract
from .creative import assemble as compile_spec
from .generation_route import ROUTES
from .project import asset_path, validate_spec
from .runner import positive_number
from .storage import digest, read
from .variation import validate_control


def require(value, message):
    if not value:
        raise ValueError(message + "；未修改项目，请用原版本核实")


def record(root, path):
    return read(inside(root, path, file=True))


def directories(root, name):
    folder = inside(root, name)
    return sorted(path for path in folder.iterdir() if path.is_dir()) if folder.exists() else []


def check_contract(root, contract, assets, require_audio_registration=True):
    validate_contract(contract)
    names = {clip["variant"] for clip in contract["clips"]}
    audio = contract["audio"]
    if require_audio_registration and "variant" in audio:
        names.add(audio["variant"])
    for name in names:
        spec = record(root, f"variants/{name}/spec.json")
        require(spec.get("id") == name, "组装引用的版本 ID 不匹配")
    if require_audio_registration and "asset" in audio:
        require(audio["asset"] in assets, "组装引用的声音素材未登记")
    return names


def check_plans(root):
    for directory in directories(root, "plans"):
        plan = record(root, directory / "plan.json")
        compiled = compile_spec(plan)
        require(plan["id"] == directory.name, "计划 ID 与目录不匹配")
        require(compiled == record(root, directory / "spec.json") ==
                record(root, f"variants/{directory.name}/spec.json"), "计划与已登记规格不一致")
        control = directory / "variation.json"
        if control.exists():
            parent = record(root, f"plans/{plan.get('parent')}/plan.json")
            value = record(root, control)
            validate_control(value, parent)
            require(value["id"] == plan["id"] and value["parent"] == plan.get("parent"), "裂变控制关联不一致")
        diff = directory / "diff.json"
        if diff.exists():
            require(record(root, diff).get("format") == "video-remix-variation-diff.v1", "不支持的裂变差异格式")


def check_assemblies(root, assets):
    for directory in directories(root, "assemblies"):
        contract = record(root, directory / "contract.json")
        check_contract(root, contract, assets)
        state = record(root, directory / "run.json")
        require(contract["id"] == directory.name == state.get("id"), "组装 ID 与目录不匹配")
        require(state.get("format") == contract["format"].replace("assembly.", "assembly-run."), "不支持的组装记录格式")
        require(state.get("contract_sha256") == contract_hash(contract), "组装合同与记录不一致")


def check_productions(root, assets):
    for directory in directories(root, "productions"):
        frozen = record(root, directory / "job.json")
        job, hashes = frozen.get("job", {}), frozen.get("spec_sha256", {})
        fields(job, ("format", "id", "assembly", "budget", "estimate_per_job"))
        require(job["format"] == "video-remix-production.v1", "不支持的直出任务格式")
        require(all(positive_number(job[key]) for key in ("budget", "estimate_per_job")), "直出预算无效")
        names = check_contract(root, job["assembly"], assets)
        require(set(hashes) == names, "直出冻结规格列表不匹配")
        for name, sha in hashes.items():
            require(digest(inside(root, f"variants/{name}/spec.json", file=True)) == sha, "直出冻结规格已变化")
        state = record(root, directory / "run.json")
        require(state.get("format") == "video-remix-production-run.v1", "不支持的直出记录格式")
        require(job["id"] == directory.name == state.get("id"), "直出 ID 与目录不匹配")
        require(state.get("job_sha256") == contract_hash(frozen) and set(state.get("variants", [])) == names,
                "直出记录与冻结任务不一致")
        if (directory / "assembly.json").exists():
            require(record(root, directory / "assembly.json") == job["assembly"], "直出组装合同已变化")
        if state.get("status") == "completed":
            saved = record(root, f"assemblies/{job['assembly']['id']}/contract.json")
            require(saved == job["assembly"], "直出完成记录缺少匹配组装")


def check_assembly_variations(root, assets):
    for directory in directories(root, "assembly-variations"):
        control = record(root, directory / "control.json")
        fields(control, ("format", "id", "parent", "changes"))
        require(control["format"] == "video-remix-assembly-variation.v1", "不支持的整片裂变格式")
        require(control["id"] == directory.name, "整片裂变 ID 与目录不匹配")
        parent = record(root, directory / "parent.json")
        # 裂变登记只冻结音轨安排；原入口允许声音素材/版本稍后登记。
        check_contract(root, parent, assets, require_audio_registration=False)
        state = record(root, directory / "run.json")
        # 历史 run 没有 format；合同和控制记录才是格式真源。
        require("format" not in state, "不支持的整片裂变运行记录格式")
        require(state.get("control") == control and state.get("parent_contract") == parent, "整片裂变冻结记录不一致")
        snapshots = state.get("parent_specs", {})
        # parent_specs 原入口只冻结画面镜头，不包含额外的连续声音 variant。
        require(set(snapshots) == {clip["variant"] for clip in parent["clips"]}, "整片裂变父规格列表不匹配")
        for name, frozen in snapshots.items():
            path = inside(root, f"variants/{name}/spec.json", file=True)
            require(frozen.get("spec") == read(path) and frozen.get("sha256") == digest(path), "整片裂变父规格已变化")
        contract = state.get("contract", {})
        validate_contract(contract)
        require(contract["id"] == directory.name, "整片裂变子合同 ID 不匹配")
        for name in state.get("registered_variants", []):
            record(root, f"plans/{name}/plan.json")
            record(root, f"variants/{name}/spec.json")
        if state.get("status") == "prepared":
            check_contract(root, contract, assets, require_audio_registration=False)
            require(record(root, directory / "assembly.json") == contract, "整片裂变子合同缺失或变化")


def inspect(root):
    root = Path(root).resolve()
    project = read(root / "project.json")
    if project.get("format") != "video-remix-project.v1":
        raise ValueError("不支持该项目格式；请继续使用原版本")
    for asset in project["assets"].values():
        asset_path(root, asset)
    count = 0
    for directory in directories(root, "variants"):
        path = inside(root, directory / "spec.json", file=True)
        spec = read(path)
        validate_spec(spec, root, verify=False)
        require(spec["id"] == directory.name, "版本 ID 与目录不匹配")
        run_path = directory / "run.json"
        if run_path.exists() and read(run_path).get("format") != "video-remix-run.v1":
            raise ValueError("不支持该任务记录格式；未修改项目")
        count += 1
    check_plans(root)
    check_assemblies(root, project["assets"])
    check_productions(root, project["assets"])
    check_assembly_variations(root, project["assets"])
    return {"compatible": True, "project_format": project["format"],
            "variants_checked": count, "migration_required": False, "generation_routes": list(ROUTES)}
