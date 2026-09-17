# SCT Video Remix

Skill 名称：`sct-video-remix`（原名 `video-remix`）。仓库地址和后端命令保持不变，旧项目无需迁移。

**面向 AI 助手的视频复刻与裂变工具箱。**

把参考视频和商品素材交给助手，得到声画分析、商品/画面参考、可执行制作计划、复刻/裂变版本、生成原片和对比结果。支持无口播 Before/After、产品展示和有声 UGC。Skill 负责内容判断，轻量后端负责无改写装配、执行和记录。

本地候选版本 `0.4.0rc2`；已发布稳定版本仍为 `0.3.9`，不因本机更新自动发布公共仓库。不提供零密钥演示；分析和生成使用用户自己的账号。流程依据真实实验改进，**不保证任意商品一次成功，也未证明批量成功率**。Gemini 分析、宿主垫图生成与即梦原视频产物是不同节点。机器审片仅在测试时辅助定位，不是生产必经门，不能代替客户判断。候选支持分镜独立生成、明确区间硬切组装和按镜头派生整片裂变；旧项目仍固定旧版，不自动迁移。分析接口明确截断或被阻断时保留证据、标为不完整，不继续当作成功输入或自动付费重试。

候选已用真实素材走通七镜组装与仅更换一镜人物的整片派生。合同层面复用其他镜头、切点和连续音轨，不代表新生成镜头的背景、动作或人物会逐像素冻结。逐镜画面参考改善了展示关系；短镜动作仍可能拖到目标截点之后，效果重复次数也可能不遵循。组装成功不能抵消这些缺失。当前连续音轨组装不是新配音或声音克隆能力。

## 安装

把本仓库链接发给你的 AI 助手，并说：**“帮我安装 sct-video-remix Skill，检查环境，告诉我还缺什么配置。”** 手动安装包在本仓库 [Releases](https://github.com/scotti1i/video-remix/releases) 中。

需要 macOS 或 Linux、Python 3.10+ 和 Git。无需安装 Python 运行依赖。

```sh
git clone https://github.com/scotti1i/video-remix.git
cd video-remix
python3 install.py
```

默认安装到 `~/.agents/skills/sct-video-remix`。已有安装器管理的旧名称目录会整体备份，只留下启动器兼容路径，不重复注册两个 Skill；非本工具管理的目录不动。若你的客户端使用专用目录，可以指定：

```sh
python3 install.py --destination ~/.codex/skills/sct-video-remix
# 或：--destination ~/.claude/skills/sct-video-remix
```

然后在助手中说：

> 使用 sct-video-remix。参考这条视频，替换为我的商品，保留前后变化，生成一条复刻和三条有明确差异的裂变。先给我看提示词和费用，再按我的授权生成。

Skill 每次启动会检测本地后端并读取最新工作指导。即使只下载 `skills/sct-video-remix/` 文件夹，它也能从 GitHub 安装缺失的后端。

### 可选：固定本机 Skill 的默认运行时来源

本地开发或候选验证可在安装时保存默认目录和可信源，之后自然调用 Skill 无需每次重填参数：

```sh
python3 install.py --destination ~/.agents/skills/sct-video-remix \
  --runtime-home /absolute/runtime-candidate --repo /absolute/video-remix
```

安装目录的 `installation.json` 只保存 `video-remix-install.v1` 格式、绝对运行时路径及无密钥 Git 来源。仓库地址支持绝对本地路径、HTTPS 或 SSH；不接受带密码、令牌查询参数的 URL。重新运行安装器会保留已有配置，显式给出的安装参数只更新对应字段；配置损坏时停止，不静默改回其他源。该侧车不会从源码目录复制到其他用户的安装。

启动时 `--home` 优先于 `VIDEO_REMIX_HOME`，再用安装配置和标准默认目录；`--repo` 优先于安装配置，再按该运行时已有可信绑定或公共 GitHub 默认选择。无侧车的普通下载用户保持原有 GitHub 默认。配置只来自实际安装目录，不读取项目里的同名文件。

本地候选使用本地仓库的已提交 main，安装它不会发布 GitHub；其他用户只有在公共仓库更新后才会通过默认入口取得新版。已有项目仍固定原提交。若未显式覆盖运行时，旧项目锁定公共 GitHub 源、且 `~/.local/share/video-remix` 已绑定该公共源，启动器继续用这套公共运行时；其他不匹配来源仍拒绝，不凭项目锁自动信任任意仓库。

## 真实调用需要什么

| 能力 | 依赖 |
|---|---|
| 参考分析 | `GEMINI_API_KEY` + `GEMINI_MODEL`；默认 Google 原生接口，可用 `GEMINI_BASE_URL` 指向兼容网关根地址，`GEMINI_AUTH_MODE=bearer` 支持用户选定的 API Mart 等 Bearer 网关 |
| 视频生成 | 从即梦官方渠道安装 `dreamina` CLI，运行 `dreamina login`，账号有相应模型权限和积分 |
| 下载验证 | `ffprobe`，一般随 FFmpeg 安装 |
| 声音对比辅助评审 | `ffmpeg` + `ffprobe` + 同上 Gemini 配置；只听音轨，不评画面，不自动选片 |
| TikTok 链接获取 | 本机 `yt-dlp`；无法获取时可直接提供本地视频 |
| 图像生成 | 助手宿主已有的生图工具；结果保存后登记为素材 |

运行 `python3 -m video_remix doctor --account` 可检查工具与即梦余额；不会发生成请求。不自动安装或重装全局工具。密钥由运行环境或你自己的密钥管理器注入，不放进项目配置和 Git。

视频提供方当前**只实现即梦**。Gemini 用于分析；其他视频 API 需要独立适配器，当前没有假装通用的 HTTP 包装器。新增适配器接口见 [dreamina.py](video_remix/dreamina.py)。

## 更新：新项目用新版，老项目保持原版

稳定启动器：`skills/sct-video-remix/scripts/bootstrap.py`。旧版 `skills/video-remix/` 中保留脚本和指导的兼容链接，供已安装的旧启动器识别新后端。

```sh
python3 skills/sct-video-remix/scripts/bootstrap.py ensure
python3 skills/sct-video-remix/scripts/bootstrap.py check
python3 skills/sct-video-remix/scripts/bootstrap.py update
python3 skills/sct-video-remix/scripts/bootstrap.py rollback
```

- 默认信任本仓库 `main`。下载到新版本目录、核对 Git 状态并启动后端检查，然后原子切换指针。
- 正在运行的命令始终使用其启动版本；项目、账号与任务记录不随更新覆盖。
- 离线时已有健康版本可以继续运行；坏版本不会顶替当前版本。
- 回退后固定使用旧版；主动执行 `update` 才恢复自动跟随 main。也可用 `--offline` 临时跳过远端检查。
- 自动更新工作指导与后端。启动协议本身未来若不兼容，需要再运行安装器更新 Skill；不宣称编辑已加载的聊天上下文。
- 所有旧版本保留，可手动清理确认不用的版本；本工具不自动删除用户文件。

**从 0.1.0 升级：需要更新一次 Skill 启动器。** 对本仓库执行 `git pull --ff-only` 后重新运行 `python3 install.py`；或让助手按新版工作指导更新已安装 Skill。只更新后端还不能获得项目固定能力。安装器保留旧 Skill 备份，不覆盖非本工具管理的目录。

通过托管 Skill 新建项目时，`runtime-lock.json` 记录可信仓库和完整 Git 提交。继续旧项目时自动使用该版本；即使默认后端已更新也不跟着切换。项目搬家或本地旧代码丢失时，从可信仓库恢复**同一提交**；离线缺版本时明确停下，不擅自换新版。

```sh
python3 skills/sct-video-remix/scripts/bootstrap.py ensure --project /path/to/project
python3 skills/sct-video-remix/scripts/bootstrap.py run --project /path/to/project -- status
python3 skills/sct-video-remix/scripts/bootstrap.py project-upgrade --project /path/to/project
# 检查确认后显式升级：
python3 skills/sct-video-remix/scripts/bootstrap.py project-upgrade --project /path/to/project --apply
```

项目升级先阻止运行中或状态不明的生成任务，再做本地只读兼容检查；通过后备份项目配置和任务记录，最后切换版本锁。检查或备份失败保持旧锁，媒体不改动。**当前只支持兼容格式升级，不会自动转换不兼容的数据。** 检查验证可读格式与规格，不代表新平台接口和成片效果已验证；也不能保证外部模型、CLI、Python 或 GitHub 永久可用。保留 Git 提交历史和已发布版本，不强推删除旧代码。

0.1.0 旧项目首次使用新启动器时按已有任务中的后端提交固定；任务版本不一致或缺失则提示核对，不自动猜版本。显式开发目录和直接调用后端属于开发方式，不提供托管安装的完整生命周期保证。

运行时默认放在 `~/.local/share/video-remix/`，可用 `VIDEO_REMIX_HOME` 改变。你自己的开发目录用 `VIDEO_REMIX_ENGINE` 或 `--engine` 显式指定，自动更新不会改写它。

Fork 用户通过启动器 `--repo <自己的 Git URL>` 选择源，并使用独立 `--home`；不把已有运行时静默改绑另一源。

## 每个项目独立管理

项目可以放在任意用户目录，后端从当前目录向上自动查找 `project.json`，也支持 `--project`。

```text
my-product/
  project.json          素材索引
  runtime-lock.json     项目固定的仓库与 Git 提交
  .runtime-backups/     主动升级前的配置与记录备份
  assets/               输入文件与 SHA-256
  analysis/             Gemini 原始返回、分析、调用记录
  plans/<id>/           制作计划与原文装配的生成规格
  variants/<id>/
    spec.json           提示词、模型、输入、裂变父版本和改变项
    run.json            提交 ID、状态、费用、后端版本、人工选择
    raw.mp4             原始结果
  assemblies/<id>/     候选：组装合同、输入指纹/任务来源、处理记录与组装结果
  compare.html          条件与视频对照文档
```

通过 `replica / variation / rerun` 区分复刻、设计裂变和同条件重生成。已有版本不覆盖，新增版本关联父版本。

完整命令和版本 JSON 字段见 [工作指导](skills/sct-video-remix/references/workflow.md)。根目录运行 `python3 -m video_remix --help` 查看入口。

`run` 默认输出执行预览，加 `--execute` 才真实提交。`--wait 30` 等待最多约 30 秒的轮询窗口（单次平台请求可能更长）；重复执行同一版本只恢复原任务，不会重新生成。

0.3.0 的 `compile /absolute/plan.json` 将一份声画制作计划直接装配、登记为生成规格，不调用模型改写。计划显式区分 `image_text`（仅图文）、`image_audio`（图＋音频）、`video_reference`（含视频），防止把混入参考的实验称为纯文字能力。字段见 [计划合同](skills/sct-video-remix/references/plan-contract.md)，创作方法见 [制作指导](skills/sct-video-remix/references/production.md)。直接 `variant` 老规格继续兼容。

同批生成默认串行；上一条尚未下载时返回 `stop=pending`，恢复同一批继续。状态保留真实队列字段；`fail` 正确视为终止失败，未知状态不伪称“生成中”。

0.3.1 补上参考表达承接、分析校正与有声制作指导。分析签名相同则复用成功结果，`analyze --refresh` 才显式重做。`assess <版本ID> --reference <素材ID> --focus sound --brief <具体声音目标> --model <模型>` 对比真实原速音轨，保留辅助意见与证据，不把转录完整或模型偏好当真人感验收。它使用分析接口、不会重新生成视频。字段装配一致性与成片表达一致性是两件事。

0.3.2 增加 `vary <控制文件.json>`：保留父计划核心，仅按明确字段替换场景/人物/穿搭及必要图片，保存原值、新值和差异，不自动重写声音与节奏。`assess --focus performance` 同次比较完整参考视频与成片，检查拍摄关系、动作因果和声画错位，支持无口播；仅用于分析，不会把视频塞入生成输入。纯图文失败定位与能力分流见 [重建指导](skills/sct-video-remix/references/image-text-reconstruction.md)。

0.3.3 修正端到端目标承接：用户上传同款参考时默认保留原话，分析分别记录音色、收音与关键过渡；评估自动消费冻结计划中已有的用户目标与源区间映射，临时检查重点不能自行扩大允许变化。旧版本没有这些记录时仍可评估，但不伪造原目标。Skill 回归从原素材独立重跑，不以手工救场样片证明流程成功。

0.3.4 将关键前后状态与必要中间态纳入参考图选择，收敛准备动作负载，核对声音意群与动作锚点的时间冲突；声音指导先明确表达任务，再写少量音色特征。辅助评审区分观察与原因，不凭生成标签推断硬切或音频来源。以上是待真实成片检验的制作改进，不是成功率承诺。

0.3.5 为 `analyze` 和 `assess --focus performance` 增加可选 `--video-fps`：完整原文件不变，显式请求服务端的分析抽帧率，并独立缓存/记录。快速翻转或短暂效果可用更密观察验证，但增加分析用量，网关接受不等于一定遵循；不改变视频生成条件或把机器报告升级为人工验收。省略参数保持旧行为，音轨专项拒绝此参数。

0.3.6 修正复刻/裂变的默认身份边界：默认更换原人物，明确要求保留时例外；画面参考与提示词同步，商品图模特不自动指定成片人物。同款保留的是商品身份，不等于保留原人物。只露手按新手部模特处理，无人镜头不额外加人。这是制作默认行为修正，不是变形率改善证明；旧项目仍按固定版本运行。

0.3.7 合并真实多品类测试暴露的两处制作遗漏：参考图明确可辨面向/部件归属及拼图状态；裁短台词同时检查语流过满与稀释，不把删减后的原句自动铺满目标时长。不新增节点或持久格式，仍需独立成片验证，不能把修订当作效果已改善。

0.3.9 仅收紧制作指导的语义核对：以实际图片核对接触点，用可辨认部件消除转向歧义；原文传输不等于图文一致或动作可执行。不增加字段或机器审片关卡，不将“更短提示词更稳”写成未经证实的规则。

0.3.8 合并端到端交接修复：分析保留各组有效商品展示，商品描述承接成对方向、部件连接与接触关系；换人须落实到实际图文而非只改发型，声音沿用原表达而非统一轻快化。`vary` 可在换图时显式更新角色说明，提交新增 `generation_inputs` 保留真正发送的角色；同条件重生成的可选诊断沿已核验父链继承目标。CLI 错误保留安全分类，不泄露原始鉴权信息；提交不明仍不自动重交。用户指定短片才节选，不以模型上限静默压缩全片。旧格式与固定版本继续有效，不将软件测试宣称为生成成功率提高的证据。

恢复旧任务不再要求原生成输入仍可用，也不依赖新提交的余额/模型帮助检查；完成态会验证本地原片，新下载仍检查工具与盘位。新登记的 `rerun` 必须和父版的生成条件相同，有改动登记为 `variation`，不把改条件试验混成随机重生成。历史记录保留，不强行重写。

预算按本次选定版本的累计费用控制后续提交，无法强制平台限制单条实际扣费。超过单条预估就停止后续提交。提交结果不明时停在 `submit_intent`，核对平台后用 `attach` 绑定原任务。

## 开发与发布边界

候选新增 `assemble <assembly.json>`（默认预览，`--execute`本地执行）。按原片真实镜头独立使用既有 `compile/run`，组装合同指定各版本取用区间和声音方式；只硬切、原速、同分辨率，保留每镜原片与任务记录。支持各镜自带声、明确连续音轨或静音；`video-remix-assembly.v1` 连续声音来自本项目生成版本，v2 另支持已登记音频/视频素材并记录来源，不改变旧合同语义。没有自动配音、声纹匹配、词级对齐或连续长镜头续接。见[分镜路线](skills/sct-video-remix/references/shot-generation.md)。单镜返修通过新版本与新组装ID，不重生其他好镜头。此软件能力不等于已经验证形变率下降或声音生成更自然。

本地开发候选增加 `python3 -m video_remix inventory --root /absolute/project-collection`，只读列出各项目的任务、实际记录费用与本地原片；保留失败、状态冲突和未知费用，不调用平台、不升级旧项目。它用于核对跨批交付遗漏，不代表成片或客户验收。实验迭代方法见 [实验迭代](skills/sct-video-remix/references/iteration.md)；候选制作修改仍需真实成片验证，未发布为新的效果升级版本。

```sh
python3 -m unittest discover -s tests -v
python3 -m video_remix doctor
```

测试使用临时 Git 仓库与假的平台响应验证更新、回退、预算和恢复；这些是内部软件测试，不是无密钥产品 demo，也不是成片质量证据。

本仓库不携带个人商品图、第三方视频、实验日志或登录凭据。公开示例素材需确认许可；用户自己的素材保存在仓库外。飞书导出由助手的飞书工具完成，后端不强制绑定飞书。

许可证：[MIT](LICENSE)。第三方模型、CLI 和输入素材分别适用其自己的使用条款。
