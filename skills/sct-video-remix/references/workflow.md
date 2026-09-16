# 后端操作与制作方法

Skill 已更名为 `sct-video-remix`。安装器接受旧目录名称并迁移到同级新目录，保留旧启动器路径兼容；后续使用安装器返回的新路径。仓库地址与后端项目版本锁不变。

以下命令均是 `bootstrap.py run --` 后的参数。`--project <目录>` 位于子命令前，启动器会读取该项目版本锁。不要把用户项目放在 engine 目录里，也不要对已有项目强行传最新版 `--engine`。

## 从 0.1.0 升级启动器

若已安装的 `bootstrap.py --help` 没有 `project-upgrade`，这是旧启动器。用本次 ensure 返回的可信 engine 下的 `install.py --destination <当前 Skill 绝对目录>` 更新 Skill（保留安装备份），再用新启动器 `ensure --project <目录>` 解析项目。不要让旧启动器把新版后端直接用于旧项目。只下载 ZIP 手动安装、没有安装器管理标记的目录不覆盖，提示用户保留旧目录后安装新包。

## 项目版本

新项目自动生成 `runtime-lock.json`，旧项目按原任务记录的 Git 提交固定版本；没有任务记录的项目经兼容检查后固定当前版。历史版本冲突或无法判断时明确报告，不猜测。将版本锁和项目文件一起备份或搬到另一台机器，可从可信源恢复同一提交。

`bootstrap.py update` 只更新默认后端。用户要升级已有项目时：先 `project-upgrade --project <目录>` 查看兼容结果，再加 `--apply`。有未结束的视频任务时先用旧版完成；不兼容不迁移，当前没有跨格式自动转换器。成功切换前的配置和记录保存在项目 `.runtime-backups/`，媒体不复制也不改写。

## 1. 项目与参考

开始制作、复刻或裂变时完整读取 [制作方法](../../sct-video-remix/references/production.md)。使用 `compile` 或 `vary` 时读取 [制作计划合同](../../sct-video-remix/references/plan-contract.md)；有口播按制作方法路由读说话指导。链接兼容新旧 Skill 路径。故障查询和单纯下载不必重新分析素材。当前工作指导与后端为 0.3.7；旧项目升级前继续遵守其固定版。

```sh
init <用户项目目录> --name "商品前后对比"
--project <目录> asset ref <本地参考.mp4> --role "前后变化与转场参考"
--project <目录> asset product <商品.jpg> --role "商品外观"
```

用户给 TikTok 链接时，先确认是分析参考与原创商品内容；使用已安装的 yt-dlp 获取用户有权使用的素材，网址作为独立参数传递。平台下载被阻断时请用户提供本地文件。不要把第三方逐字稿改写成新商品文案。

Gemini 分析真实调用：

```sh
--project <目录> analyze ref --model <账户支持的模型名> --brief "先判断视频类型及有无人声；提取实际镜头、声画节拍、动作因果、光线与需要保留的表达机制；区分观察与推断"
```

密钥通过 `GEMINI_API_KEY` 注入；`GEMINI_BASE_URL` 可指定 Google-compatible 网关根地址，默认 Google 官方。`GEMINI_AUTH_MODE=google` 默认使用 x-goog-api-key；用户选择 API Mart 等 Bearer 网关时设 `GEMINI_AUTH_MODE=bearer`（不是自动切供应商）。`GEMINI_MODEL` 可替代 --model。不要把 key 写进命令文本、版本 JSON 或笔记。原始响应和分析文本均落项目目录。按素材哈希、区间和分析目的复用已有分析；已有分析缺关键维度时只补这一维，不每步重新上传视频。

0.3.1 起，相同素材 SHA、模型、网关、完整分析模板与 brief 的成功分析会命中缓存，返回 `cache_hit: true`，不重复发请求。需要重新观察时显式加 `--refresh`，保留旧结果；旧版没有签名的记录仍可人工读取复用，不自动猜等价。区间以实际登记的视频文件为准，改变区间需明确准备/登记媒体。若旁边有 `corrections.md`，缓存结果同时返回其路径，制作时与原分析一起读取。

0.3.5 的 `analyze` 与 `assess --focus performance` 可选 `--video-fps 5` 请求更密的视频观察，范围大于0且不超过24；不是生成帧率，不转码、改速或裁剪原文件，也不把参考视频传进生成。省略时沿用服务端默认。[Gemini 官方说明](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding)默认约1fps可能漏快速动作，支持 `videoMetadata.fps`；网关是否采用该参数仍需实测，记录只承诺请求值，不伪称已获逐帧观察。快速翻转、短暂触发与恢复可有理由选更密采样，不每条默认增加费用。帧率进入分析缓存签名；不同帧率不会复用为同一观察。参数被拒绝就报告，不静默去掉参数重试；`sound` 不接受此视频参数。

## 2. 生成提示词和裂变

先读分析与用户商品资料，写一条复刻，再按需求设计变化。Before/After 优先控制：相同主体与机位、前后差异可辨认、变化发生的方式、揭晓节奏。先确认产品支持什么效果，再填入后态。

区分两个分镜概念：叙事上按 Before / 变化 / After 理解；生成时按模型时长和实际切镜拆分，不把三个阶段强行变成三个切镜。

若参考含剪辑贴片，单独列为后期项；不要指望视频模型稳定渲染长字幕。涉及连续变形时，说明需要连续变化还是剪辑切换。

需要垫图时使用宿主已可用的生图能力；保存真实结果，再登记为 image 素材。不要求额外人物、场景或九宫格，除非这条镜头确实需要。

完整制作建议先写一份 `video-remix-plan.v1`，再执行 `compile <绝对路径.json>`：原文装配输入角色、外观、声画描述和实际镜头，保存计划快照并登记版本，**不做二次改写、不提交生成**。计划格式见 [制作计划合同](../../sct-video-remix/references/plan-contract.md)。老的直接 `variant` 路径仍兼容，可用于已有成型 prompt，不强制给简单任务补文书。

直接版本字段合同：

```json
{
  "id": "replica-01",
  "kind": "replica",
  "provider": "dreamina",
  "model": "seedance2.0fast_vip",
  "duration": 7,
  "ratio": "9:16",
  "resolution": "720p",
  "inputs": [
    {"type": "image", "asset": "product", "role": "商品外观"},
    {"type": "video", "asset": "ref", "role": "变化过程与转场节奏"}
  ],
  "prompt": "根据真实参考与商品填写完整提示词。@image1 指商品，@video1 指变化节奏。"
}
```

这是字段示例，不是可用的示范成片或零密钥 demo。运行前填写真实提示词。平台分辨率、时长、输入限制以本机 `dreamina multimodal2video --help` 为准。各类型分别编号，顺序与 inputs 一致。

设计裂变使用 `kind: variation`，指定 `parent: replica-01` 和 `change`；同条件重生成使用 `kind: rerun`。不覆盖已有规格；新版本使用新 ID。

要保留核心机制、仅换场景/人物/穿搭时，优先用结构化控制入口，字段及示例见[制作计划合同的受控裂变](../../sct-video-remix/references/plan-contract.md#只换场景人物或穿搭)：

```sh
--project <目录> asset terrace-anchor <新场景垫图.png> --role "新场景与构图参考"
--project <目录> vary /absolute/controls.json
```

控制文件指定父计划、新 ID、不可变的商品素材和 scene/person/outfit 文字/图片替换。`vary` 原样继承父版声画时间、声音内容、输入模式和生成参数，保存计划/规格、控制快照与前后差异；不自动生图、不调用 Gemini、不提交即梦。检查差异与新素材后，用下一节的同一 `run` 命令按已有预算生成。软件保证替换边界，模型是否保持核心机制仍需看真实成片。

## 3. 提交与恢复

```sh
--project <目录> run replica-01 variant-01 --budget <本轮总积分> --estimate-per-job <单条预估>
--project <目录> run replica-01 variant-01 --budget <本轮总积分> --estimate-per-job <单条预估> --execute --wait 30
```

首条打印真实输入和提示词，不提交。执行参数由用户已授权的版本和预算确定。历史预估不是实时价格；平台回报超过预估就停后续提交。预算覆盖本命令列出的全部版本，恢复时已发生费用也计入；无法取消已经发生的超预估扣费。

等待结束仍在生成时，重复同一条 run 命令恢复；有任务 ID 就不会重交。模型失败后如需再生成，登记新的 rerun 版本，费用独立计数。

同批任务默认串行：上一条未完成下载时返回 `stop=pending`，不抢先下后一单；恢复同一批即可继续。平台 `fail` 也是终止失败，不是仍在生成。查看 `queue_info.queue_status` 区分 Queueing 与 Generating，没有字段就说未知，不凭 querying 猜测。已知失败保存安全错误分类，不盲目切模型/新建重试；用户授权的补测才新建版本。

0.2.x 旧记录若已有 task_id 且查询证据为 `provider_status=fail`、但 phase 错留 polling，新启动器允许备份后兼容升级，不改写旧证据。只有 querying 或 submit_intent 仍阻止升级；先用原版查原任务取得真实终止结果，不能手改成失败绕过。

若标记 submit_intent（结果不明），先从平台查到原任务，使用 `attach <版本ID> <任务ID>`；它会核对任务 ID 和完整提示词再绑定。查不到时停下，请用户核实，不自动重交。

## 4. 管理与交付

```sh
--project <目录> status
--project <目录> compare
--project <目录> review replica-01 selected --note "用户选中：揭晓自然"
```

`compare` 生成本地 HTML 对比文档，左条件、右原始视频；直接把文件链接交给用户。若用户指定飞书，可用宿主的飞书工具上传同一原文件，后端不依赖飞书。

先交付视频，再写简短结论。运行成功仅表示文件生成，不表示商品效果或自然度合格。

有声结果可做一次目标明确的音轨对比（使用 Gemini 分析额度，不是即梦生成积分）：

```sh
--project <目录> assess <版本ID> --reference <参考视频素材ID> --focus sound \
  --brief "具体说明用户要保留的声音表达、紧凑感，以及允许改变什么" --model <模型>
```

`sound` 需要 ffmpeg、ffprobe 和相同 Gemini 配置。它校验参考和生成原片哈希，提取完整原速第一音轨、保留声道、转成 24kHz PCM，单份超过 16 MiB 则停止，不自动裁剪/降噪/变速。两条音轨在一次请求中比较，记录输入、模型、用量和诊断；请求失败不自动重试，先看已有 run/原始响应。每次 `assess` 是一次新的检查，不要因尚未看到结果重复调用。

返回的 `assessment` 是带声明的辅助意见，`raw_analysis` 是 Gemini 原文。它不会写入人工选择，不评画面或连续动作，不保证秒数和主观判断正确。将辅助结果与抽帧/动态检查、用户意见一起形成简短交付，仍以真实原片供用户判断。比较时提供原参考及新成片，不只贴机器结论。

0.3.3 起，两种评估会自动读取对应冻结计划中已有的 `provenance.target` 和 `source_to_output`，记录来源与摘要；先核对计划与生成规格一致，不把不匹配的计划当验收依据。`brief` 只指定这轮检查焦点，不能覆盖原目标或事后增加豁免。没有计划/目标的旧版本保持旧行为，明确只有本轮 brief；软件不会替你证明创作方记录的目标忠于用户，也不把历史目标当不可篡改审计日志。

整体模仿或声画错位问题可改用 `--focus performance`，`--brief` 写核心保留项、允许的场景/人物变化和必要源区间映射。它用 ffprobe 核对视频，按原字节同次上传完整参考与成片（支持无音轨），不转码、不剪短、不改速。单份超过 40 MiB 或总计超过 64 MiB 则停止并说明；不要为过限偷偷改源区间。原始视频哈希、媒体信息、请求用量与辅助报告均落盘。此处视频仅进入 Gemini 分析，不改变 Seedance 的图片文字输入。模型可能漏看快动作，不给自动总分/selected，不把它当精确运动测量。

## 视频平台扩展

当前只实现即梦视频适配器。Gemini 是分析接口，不是第二个视频供应商。新增视频平台实现 `account / arguments / submit / query / download`，返回相同任务与费用语义；先根据该平台真实文档实现，不伪装所有接口都兼容。不要自动安装付费平台或切到未经用户指定的供应商。
