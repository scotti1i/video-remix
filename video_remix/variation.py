"""只替换显式场景、人物、穿搭插槽；其余父计划逐字保留。"""

from copy import deepcopy
import re

from .creative import assemble, store_plan
from .storage import digest, read, slug


SLOTS = frozenset(("scene", "person", "outfit"))
TEXT_PATH = re.compile(r"(?:look|constraints|shots\.(0|[1-9][0-9]*)\.(?:action|camera))\Z")


def require_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} 仅接受字段：" + ", ".join(keys))


def validate_control(control, base):
    require_keys(control, ("format", "id", "parent", "frozen_core", "slots"), "裂变控制")
    if control["format"] != "video-remix-variation.v1":
        raise ValueError("不支持的裂变控制格式")
    slug(control["id"])
    if control["id"] == control["parent"]:
        raise ValueError("裂变必须使用新 ID")
    core = control["frozen_core"]
    require_keys(core, ("mechanism", "product_assets"), "frozen_core")
    if not isinstance(core["mechanism"], str) or not core["mechanism"].strip():
        raise ValueError("填写要保留的参考核心机制")
    protected = core["product_assets"]
    actual = {item["asset"] for item in base["inputs"] if item["type"] == "image"}
    if not isinstance(protected, list) or not protected or any(
            not isinstance(asset, str) or asset not in actual for asset in protected):
        raise ValueError("product_assets 必须列出父计划实际使用的商品图片素材 ID")
    slots = control["slots"]
    if not isinstance(slots, dict) or not slots or set(slots) - SLOTS:
        raise ValueError("slots 只允许 scene / person / outfit，至少修改一项")
    for name, slot in slots.items():
        if not isinstance(slot, dict) or not slot or set(slot) - {"text", "images"}:
            raise ValueError(f"{name} 只允许 text / images 替换列表")
        if any(not isinstance(items, list) for items in slot.values()) or not any(slot.values()):
            raise ValueError(f"{name} 至少包含一个显式替换")


def text_location(plan, path):
    if not isinstance(path, str) or not TEXT_PATH.fullmatch(path):
        raise ValueError("只允许替换 look、constraints、shots.<序号>.action/camera 中的具体短语")
    parts = path.split(".")
    if len(parts) == 1:
        container, key = plan, path
    else:
        index = int(parts[1])
        if index >= len(plan["shots"]):
            raise ValueError("替换指向不存在的镜头")
        container, key = plan["shots"][index], parts[2]
    if not isinstance(container.get(key), str):
        raise ValueError(f"父计划没有可替换的文字字段：{path}")
    return container, key


def replace_text(base, child, slots):
    edits = {}
    for name, slot in slots.items():
        for entry in slot.get("text", []):
            require_keys(entry, ("path", "from", "to"), f"{name}.text")
            before, after, path = entry["from"], entry["to"], entry["path"]
            if not all(isinstance(v, str) and v.strip() for v in (before, after)) or before == after:
                raise ValueError("文字替换需要不同的非空 from / to")
            container, key = text_location(base, path)
            source = container[key]
            if source.count(before) != 1:
                raise ValueError(f"{path} 中原短语须恰好出现一次；请填写更完整的短语")
            start = source.index(before)
            edits.setdefault(path, []).append((start, start + len(before), after))
    for path, operations in edits.items():
        container, key = text_location(child, path)
        ordered = sorted(operations)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError(f"{path} 的替换短语重叠，请合并为一项")
        for start, end, after in reversed(ordered):
            container[key] = container[key][:start] + after + container[key][end:]


def replace_images(base, child, control):
    protected, replaced = set(control["frozen_core"]["product_assets"]), set()
    for name, slot in control["slots"].items():
        for entry in slot.get("images", []):
            if not isinstance(entry, dict) or not {"from", "to"} <= entry.keys() or \
                    entry.keys() - {"from", "to", "role"}:
                raise ValueError(f"{name}.images 需要 from / to，可选 role")
            if "role" in entry and (not isinstance(entry["role"], str) or not entry["role"].strip()):
                raise ValueError("新图片 role 必须是非空文字")
            before, after = entry["from"], entry["to"]
            if not all(isinstance(v, str) and v for v in (before, after)) or before == after:
                raise ValueError("图片替换需要不同的 from / to 素材 ID")
            if before in protected or before in replaced:
                raise ValueError("不能替换商品素材，或重复替换同一输入")
            matches = [i for i, item in enumerate(base["inputs"])
                       if item["asset"] == before and item["type"] == "image"]
            if len(matches) != 1:
                raise ValueError("图片替换须指向父计划唯一的 image 输入；不能替换音视频")
            child["inputs"][matches[0]]["asset"] = after
            if "role" in entry:
                child["inputs"][matches[0]]["role"] = entry["role"]
            replaced.add(before)


def differences(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            result.extend(differences(before.get(key), after.get(key), f"{path}.{key}".lstrip(".")))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        result = []
        for index, (old, new) in enumerate(zip(before, after)):
            result.extend(differences(old, new, f"{path}.{index}"))
        return result
    return [] if before == after else [{"path": path, "before": before, "after": after}]


def vary(root, file):
    control = read(file)
    if not isinstance(control, dict):
        raise ValueError("裂变控制必须是 JSON 对象")
    parent = slug(control.get("parent", ""))
    source = root / "plans" / parent / "plan.json"
    if not source.is_file():
        raise ValueError("父版没有已编译制作计划；先准备与父规格一致的计划，不能从 prompt 猜回")
    base = read(source)
    spec_file = root / "variants" / parent / "spec.json"
    run_file = spec_file.parent / "run.json"
    if run_file.exists() and read(run_file).get("spec_sha256") != digest(spec_file):
        raise ValueError("父版已运行后的规格发生变化，先核实原始记录")
    if assemble(base) != read(spec_file):
        raise ValueError("父计划与冻结规格不一致，先核实原始记录")
    validate_control(control, base)
    child = deepcopy(base)
    replace_text(base, child, control["slots"])
    replace_images(base, child, control)
    child.update(id=control["id"], kind="variation", parent=parent,
                 change="受控替换：" + ", ".join(control["slots"]))
    changes = differences(base, child)
    report = {"format": "video-remix-variation-diff.v1", "parent": parent,
              "parent_plan_sha256": digest(source), "frozen_core": control["frozen_core"],
              "changes": changes,
              "note": "仅证明指定字段替换，其余计划保留；不证明描述/图片无语义冲突或模型效果合格"}
    result = store_plan(root, child, {"variation.json": control, "diff.json": report})
    directory = root / "plans" / child["id"]
    return {**result, "diff": str(directory / "diff.json"),
            "control": str(directory / "variation.json"), "changes": changes}
