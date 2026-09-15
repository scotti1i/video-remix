"""把制作计划直接装配为生成规格；不再让模型多轮摘要表演信息。"""

from collections import Counter

from .project import add_variant, validate_rerun, validate_spec
from .storage import atomic, read, slug


MODES = {"image_text", "image_audio", "video_reference"}


def validate_plan(plan):
    if plan.get("format") != "video-remix-plan.v1":
        raise ValueError("不支持的制作计划格式")
    slug(plan["id"])
    if plan.get("mode") not in MODES:
        raise ValueError("mode 必须是 image_text / image_audio / video_reference")
    counts = Counter(item["type"] for item in plan.get("inputs", []))
    mode = plan["mode"]
    if mode == "image_text" and (counts["video"] or counts["audio"]):
        raise ValueError("image_text 只允许图片输入，不能暗中携带音视频参考")
    if mode == "image_audio" and (counts["video"] or not counts["audio"]):
        raise ValueError("image_audio 需要音频参考且不能输入视频")
    if mode == "video_reference" and not counts["video"]:
        raise ValueError("video_reference 需要实际视频素材")
    for field in ("intent", "product", "look", "sound"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise ValueError(f"制作计划缺少 {field}；无声视频在 sound 明确写无口播")
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("缺少实际镜头计划，不强制固定镜头数")
    previous = 0
    duration = plan.get("duration")
    if type(duration) is not int or duration <= 0:
        raise ValueError("duration 必须是正整数")
    for shot in shots:
        start, end = shot.get("start"), shot.get("end")
        if any(type(v) not in (int, float) for v in (start, end)):
            raise ValueError("镜头时间必须是数字")
        if not (previous <= start < end <= duration):
            raise ValueError("镜头时间须有序、不重叠且在成片时长内")
        if not isinstance(shot.get("action"), str) or not shot["action"].strip():
            raise ValueError("每镜缺少动作／变化描述")
        previous = end


def assemble(plan):
    validate_plan(plan)
    references, counts = [], Counter()
    for entry in plan["inputs"]:
        counts[entry["type"]] += 1
        references.append(f"@{entry['type']}{counts[entry['type']]}: {entry['role']}")
    sections = [f"{plan['duration']} seconds; {plan['ratio']}; {plan['mode']}.",
                "INPUT ROLES\n" + "\n".join(references)]
    for label, key in (("INTENT", "intent"), ("PRODUCT", "product"),
                       ("CAPTURE / LOOK", "look"), ("SOUND / DELIVERY", "sound")):
        sections.append(label + "\n" + plan[key])
    for shot in plan["shots"]:
        lines = [f"[{shot['start']}–{shot['end']}s] {shot['action']}"]
        for key in ("camera", "delivery", "sync"):
            if shot.get(key):
                lines.append(f"{key}: {shot[key]}")
        sections.append("\n".join(lines))
    if plan.get("constraints"):
        sections.append("CONSTRAINTS\n" + plan["constraints"])
    fields = ("id", "kind", "parent", "change", "provider", "model",
              "duration", "ratio", "resolution", "inputs")
    return {**{key: plan[key] for key in fields if key in plan},
            "prompt": "\n\n".join(sections), "creative_mode": plan["mode"]}


def compile_plan(root, file):
    plan = read(file)
    spec = assemble(plan)
    validate_spec(spec, root)
    validate_rerun(spec, root)
    target = root / "plans" / spec["id"]
    if target.exists() or (root / "variants" / spec["id"]).exists():
        raise ValueError("计划或版本已存在，请使用新 ID")
    target.mkdir(parents=True)
    atomic(target / "plan.json", plan)
    atomic(target / "spec.json", spec)
    result = add_variant(root, target / "spec.json")
    return {**result, "plan": str(target / "plan.json"),
            "note": "原文装配，无二次改写；未提交生成"}
