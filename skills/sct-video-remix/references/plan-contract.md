# 制作计划合同 v1

`compile /absolute/plan.json` 逐字装配 prompt 并登记一个版本，不运行外部模型。正文只写希望模型执行的指令；分析来源、推测、替换说明放 `provenance`，它们随计划保存但不混进 prompt。

例子用于字段说明，实际任务必须换成真实商品与镜头：

```json
{
  "format": "video-remix-plan.v1",
  "id": "replica-01",
  "kind": "replica",
  "provider": "dreamina",
  "model": "seedance2.0fast_vip",
  "mode": "image_text",
  "duration": 15,
  "ratio": "9:16",
  "resolution": "720p",
  "inputs": [
    {"type": "image", "asset": "anchor", "role": "画面参考，不是强制首帧"},
    {"type": "image", "asset": "product", "role": "商品结构与配色真源"}
  ],
  "intent": "填写本次要保留的表达机制与替换边界。",
  "product": "填写图中可见或用户确认的商品外观；不推断功效。",
  "look": "填写参考实有的光线、机位、手持和质感。",
  "sound": "填写有无口播、原创台词、声线/语速/重音/停顿及现场声。",
  "shots": [
    {"start": 0, "end": 15,
     "action": "真实动作起因→接触/峰值→自然收尾。按素材增减镜头，不固定一镜。",
     "camera": "机位及真实切镜/连续运动。",
     "delivery": "本镜台词/表达方向，无口播可省略。",
     "sync": "关键声音/动作/切镜关联；不逐词指定表情。"}
  ],
  "constraints": "必要的禁止项，不堆无关负面词。",
  "provenance": {
    "analysis": "analysis/real-run/analysis.md",
    "source_interval": [0, 15],
    "audio_change": "记录是否移除/更换原音轨及原因",
    "retiming": "记录是否改速/改时长",
    "source_to_output": [{"source": [0, 15], "output": [0, 15], "reason": "示例映射；按实际剪取填写"}],
    "uncertain": []
  }
}
```

`intent/product/look/sound` 和各镜 `action/camera/delivery/sync` 原文进入 prompt；角色按每种类型实际顺序变成 `@image1` / `@video1` / `@audio1`，不由 agent 猜编号。`constraints` 可省略。

字段名 `mode` 是真实输入条件：`image_text` 禁止音视频输入；`image_audio` 需要音频且禁止视频；`video_reference` 需要视频。不要只改标签、不改真实 inputs。角色只规定使用意图，不是平台隔离外观/动作的确定性开关。

`shots` 用输出时间，`provenance.source_interval` 用原片时间。允许一镜连续变化或多镜，不强制三拍；镜头有序、不重叠、不超输出时长。声音可跨切镜延续，在 sound 或 sync 说明。

有口播时在 `sound` 保存唯一台词正文及少量意群的近似时间/表达走势；`delivery/sync` 只接关键意思，不重复另一套台词或与全局指令相反的语气。意群独立于切镜，具体做法见 [说话表达迁移](../../sct-video-remix/references/speech.md)。`provenance` 可增加 `target`、`transfer`、`acceptance` 等短记录，解释用户目标、源证据如何进入本计划、结果该看什么；它们只帮助代理承接，不是新增格式门禁，也不会进入生成 prompt。

若跳删/变速，`provenance.source_to_output` 可逐段记录源区间与输出区间，供验收定位；它是制作决策的证据，不是视频模型精确时序保证，不要求简单任务补映射表。

`kind=variation` 需要已存在的 `parent` 和 `change`；`rerun` 从父版冻结的 `variants/<id>/spec.json` 复制，只改新 ID、kind、parent 和记录说明，不从制作摘要重写 prompt。新登记会核对提供方、模型、时长、比例、分辨率、有序 inputs 与完整 prompt 相同；改条件用 variation。旧 `variant` JSON 继续有效，新增计划不改变旧 v1 项目语义。保留的 `plans/<id>/plan.json` 和 `spec.json` 是可审查的交接快照。
