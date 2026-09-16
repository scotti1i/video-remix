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
  "sound": "填写有无口播、按用户要求保留或调整的唯一台词、音色与录音听感、语流及现场声。",
  "shots": [
    {"start": 0, "end": 15,
     "action": "真实动作起因→接触/峰值→自然收尾。按素材增减镜头，不固定一镜。",
     "camera": "机位及真实切镜/连续运动。",
     "delivery": "本镜承接的表达方向；不重复sound台词，无口播可省略。",
     "sync": "关键声音/动作/切镜关联；不逐词指定表情。"}
  ],
  "constraints": "必要的禁止项，不堆无关负面词。",
  "provenance": {
    "target": "本次用户请求及已确认的保留/替换边界；不能把制作取舍反写成用户授权。",
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

有口播时在 `sound` 保存唯一台词正文及少量意群的近似时间/表达走势；`delivery/sync` 只接关键意思，不重复另一套台词或与全局指令相反的语气。意群独立于切镜，具体做法见 [说话表达迁移](../../sct-video-remix/references/speech.md)。`provenance` 可增加 `target`、`transfer`、`acceptance` 等短记录，解释用户目标、源证据如何进入本计划、结果该看什么；它们不会进入生成 prompt。`target` 是来自用户的目标，不是作者对成片的评价；`transfer` 把关键展示组、商品关系及声音证据指到实际正文，合并或省略时记原因，不要求固定表格。`acceptance` 是历史可选字段，只记观察问题，不是机器验收结论，也不强制每次填写。可选诊断会消费冻结计划的原目标，临时 `brief` 不能自行豁免。

若跳删/变速，`provenance.source_to_output` 可逐段记录源区间与输出区间，供验收定位；它是制作决策的证据，不是视频模型精确时序保证，不要求简单任务补映射表。声音和画面采用不同源区间/变速时，分别记录，并在各条 `reason` 标明“声音”或“画面”；不能用视觉切点代替语音边界。`target` 只保存用户的内容目标和允许变化，不混入“本轮仅预览/尚未提交”等临时执行状态。

`kind=variation` 需要已存在的 `parent` 和 `change`；`rerun` 从父版冻结的 `variants/<id>/spec.json` 复制，只改新 ID、kind、parent 和记录说明，不从制作摘要重写 prompt。新登记会核对提供方、模型、时长、比例、分辨率、有序 inputs 与完整 prompt 相同；改条件用 variation。旧 `variant` JSON 继续有效，新增计划不改变旧 v1 项目语义。保留的 `plans/<id>/plan.json` 和 `spec.json` 是可审查的交接快照。

## 只换场景、人物或穿搭

用户要保留参考核心机制、只换上述元素时，用 `vary /absolute/controls.json`。它从项目中已编译的父计划派生，不调用模型、不重写声音或分镜，也不提交视频生成。控制文件使用独立格式，旧计划和规格无需迁移：

```json
{
  "format": "video-remix-variation.v1",
  "id": "terrace-01",
  "parent": "replica-01",
  "frozen_core": {
    "mechanism": "保留连续热情推荐、手持细节到上脚揭晓、俯拍走动的表达机制。",
    "product_assets": ["product"]
  },
  "slots": {
    "scene": {
      "text": [
        {"path": "look", "from": "dark asphalt", "to": "wooden terrace"},
        {"path": "shots.0.action", "from": "dark asphalt", "to": "wooden terrace"}
      ],
      "images": [{"from": "anchor", "to": "terrace-anchor"}]
    },
    "outfit": {
      "text": [{"path": "look", "from": "black leggings", "to": "blue jeans"}]
    }
  }
}
```

示例短语必须换成父计划中实际存在的原文；新垫图先登记为 `terrace-anchor` 素材。`person` 与 `scene/outfit` 的结构相同。只填写本次需要改变的插槽，各插槽可包含 `text`、`images` 或二者；未填项保持父版原样，不自动补图。

替换规则：

- `text.path` 仅允许 `look`、`constraints`、`shots.<从0起的序号>.action/camera`。`from` 是完整的字面短语，须在指定父字段中恰好出现一次；重复时写更长的原短语。所有替换同时基于父原文执行，不使用正则、不连锁替换、不跨字段搜索；重叠或未匹配即报错。
- `images` 显式替换父版一个图片输入的素材 ID；数量、类型、顺序保持。默认保留 role；0.3.8 起可用 `{"from":"anchor","to":"new-anchor","role":"新人物与新场景画面参考，非强制首帧"}` 同步替换图片角色，避免新图配旧人物/场景说明。role 原文进入实际 prompt 和差异记录，不由后端猜写。父版重复使用同一图片 ID 时，此简化入口拒绝含糊替换。不能增删输入或替换视频/音频。新素材必须已登记，商品真源 `frozen_core.product_assets` 禁止替换，包括 role。旧控制文件行为不变；新可选字段需使用支持它的固定运行时。
- `frozen_core.mechanism` 保存本次必须保留的核心机制供对照，不重复插入 prompt。提供方、模型、时长、画幅、分辨率、输入模式、`intent/product/sound`、每镜起止时间和 `delivery/sync` 以及来源映射都直接继承。不能往控制文件添加这些覆盖字段；确需改声音或节拍时，另写明确的组合变化计划。

返回新计划/规格及 `plans/<新ID>/variation.json`、`diff.json`。差异列出每个变化字段的前后原文和父计划哈希；父版保持原样。父计划须与其冻结规格一致，只有旧规格而没有计划时，不从 prompt 自动猜回结构。

提交前检查完整差异及新旧素材是否冲突：例如原垫图仍带旧服装、台词仍提黑裤子，就不能声称仅改视觉描述已经协调完成。软件冻结的是字段和替换范围；插槽文字仍可能在语义上改变动作/拍摄效果，新图片也可能带入其他变化，因此不能把 diff 通过当作模型效果或声画节奏已经保真的证据。
