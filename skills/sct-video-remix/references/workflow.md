# 后端操作与制作方法

Skill 已更名为 `sct-video-remix`。安装器接受旧目录名称并迁移到同级新目录，保留旧启动器路径兼容；后续使用安装器返回的新路径。仓库地址与后端项目版本锁不变。

以下命令均是 `bootstrap.py run --` 后的参数。`--project <目录>` 位于子命令前，启动器会读取该项目版本锁。不要把用户项目放在 engine 目录里，也不要对已有项目强行传最新版 `--engine`。

## 从 0.1.0 升级启动器

若已安装的 `bootstrap.py --help` 没有 `project-upgrade`，这是旧启动器。用本次 ensure 返回的可信 engine 下的 `install.py --destination <当前 Skill 绝对目录>` 更新 Skill（保留安装备份），再用新启动器 `ensure --project <目录>` 解析项目。不要让旧启动器把新版后端直接用于旧项目。只下载 ZIP 手动安装、没有安装器管理标记的目录不覆盖，提示用户保留旧目录后安装新包。

## 项目版本

新项目自动生成 `runtime-lock.json`，旧项目按原任务记录的 Git 提交固定版本；没有任务记录的项目经兼容检查后固定当前版。历史版本冲突或无法判断时明确报告，不猜测。将版本锁和项目文件一起备份或搬到另一台机器，可从可信源恢复同一提交。

`bootstrap.py update` 只更新默认后端。用户要升级已有项目时：先 `project-upgrade --project <目录>` 查看兼容结果，再加 `--apply`。有未结束的视频任务时先用旧版完成；不兼容不迁移，当前没有跨格式自动转换器。成功切换前的配置和记录保存在项目 `.runtime-backups/`，媒体不复制也不改写。

## 1. 项目与参考

开始制作、复刻或裂变时完整读取 [制作方法](../../sct-video-remix/references/production.md)。使用 `compile` 时读取 [制作计划合同](../../sct-video-remix/references/plan-contract.md)。链接兼容新旧 Skill 路径。故障查询和单纯下载不必重新分析素材。当前工作指导与后端为 0.3.0；旧项目升级前继续遵守其固定版。

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

## 3. 提交与恢复

```sh
--project <目录> run replica-01 variant-01 --budget <本轮总积分> --estimate-per-job <单条预估>
--project <目录> run replica-01 variant-01 --budget <本轮总积分> --estimate-per-job <单条预估> --execute --wait 30
```

首条打印真实输入和提示词，不提交。执行参数由用户已授权的版本和预算确定。历史预估不是实时价格；平台回报超过预估就停后续提交。预算覆盖本命令列出的全部版本，恢复时已发生费用也计入；无法取消已经发生的超预估扣费。

等待结束仍在生成时，重复同一条 run 命令恢复；有任务 ID 就不会重交。模型失败后如需再生成，登记新的 rerun 版本，费用独立计数。

同批任务默认串行：上一条未完成下载时返回 `stop=pending`，不抢先下后一单；恢复同一批即可继续。平台 `fail` 也是终止失败，不是仍在生成。查看 `queue_info.queue_status` 区分 Queueing 与 Generating，没有字段就说未知，不凭 querying 猜测。已知失败保存安全错误分类，不盲目切模型/新建重试；用户授权的补测才新建版本。

若标记 submit_intent（结果不明），先从平台查到原任务，使用 `attach <版本ID> <任务ID>`；它会核对任务 ID 和完整提示词再绑定。查不到时停下，请用户核实，不自动重交。

## 4. 管理与交付

```sh
--project <目录> status
--project <目录> compare
--project <目录> review replica-01 selected --note "用户选中：揭晓自然"
```

`compare` 生成本地 HTML 对比文档，左条件、右原始视频；直接把文件链接交给用户。若用户指定飞书，可用宿主的飞书工具上传同一原文件，后端不依赖飞书。

先交付视频，再写简短结论。运行成功仅表示文件生成，不表示商品效果或自然度合格。

## 视频平台扩展

当前只实现即梦视频适配器。Gemini 是分析接口，不是第二个视频供应商。新增视频平台实现 `account / arguments / submit / query / download`，返回相同任务与费用语义；先根据该平台真实文档实现，不伪装所有接口都兼容。不要自动安装付费平台或切到未经用户指定的供应商。
