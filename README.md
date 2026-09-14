# Video Remix

**面向 AI 助手的视频复刻与裂变工具箱。**

把参考视频和商品素材交给助手，得到可执行提示词、复刻/裂变版本、生成原片和对比结果。首版优先无口播的 Before / After 视频。Skill 负责内容判断，轻量后端负责执行和记录。

当前为供小规模真实使用的 `0.2.0`。不提供零密钥演示；分析和生成使用用户自己的账号。自动更新、项目版本固定与任务管理已测试；新后端的视频生成适配器使用即梦现有 CLI，尚未用新的 Before/After 商品样本验收成片效果。

## 安装

把本仓库链接发给你的 AI 助手，并说：**“帮我安装 video-remix Skill，检查环境，告诉我还缺什么配置。”** 手动安装包在本仓库 [Releases](https://github.com/scotti1i/video-remix/releases) 中。

需要 macOS 或 Linux、Python 3.10+ 和 Git。无需安装 Python 运行依赖。

```sh
git clone https://github.com/scotti1i/video-remix.git
cd video-remix
python3 install.py
```

默认安装到 `~/.agents/skills/video-remix`。支持 Agent Skills 的助手可从该目录发现；若你的客户端使用专用目录，可以指定：

```sh
python3 install.py --destination ~/.codex/skills/video-remix
# 或：--destination ~/.claude/skills/video-remix
```

然后在助手中说：

> 使用 video-remix。参考这条视频，替换为我的商品，保留前后变化，生成一条复刻和三条有明确差异的裂变。先给我看提示词和费用，再按我的授权生成。

Skill 每次启动会检测本地后端并读取最新工作指导。即使只下载 `skills/video-remix/` 文件夹，它也能从 GitHub 安装缺失的后端。

## 真实调用需要什么

| 能力 | 依赖 |
|---|---|
| 参考分析 | `GEMINI_API_KEY` + `GEMINI_MODEL`；默认 Google 原生接口，可用 `GEMINI_BASE_URL` 指向兼容网关根地址 |
| 视频生成 | 从即梦官方渠道安装 `dreamina` CLI，运行 `dreamina login`，账号有相应模型权限和积分 |
| 下载验证 | `ffprobe`，一般随 FFmpeg 安装 |
| TikTok 链接获取 | 本机 `yt-dlp`；无法获取时可直接提供本地视频 |
| 图像生成 | 助手宿主已有的生图工具；结果保存后登记为素材 |

运行 `python3 -m video_remix doctor --account` 可检查工具与即梦余额；不会发生成请求。不自动安装或重装全局工具。密钥由运行环境或你自己的密钥管理器注入，不放进项目配置和 Git。

视频提供方当前**只实现即梦**。Gemini 用于分析；其他视频 API 需要独立适配器，当前没有假装通用的 HTTP 包装器。新增适配器接口见 [dreamina.py](video_remix/dreamina.py)。

## 更新：新项目用新版，老项目保持原版

稳定启动器：`skills/video-remix/scripts/bootstrap.py`。

```sh
python3 skills/video-remix/scripts/bootstrap.py ensure
python3 skills/video-remix/scripts/bootstrap.py check
python3 skills/video-remix/scripts/bootstrap.py update
python3 skills/video-remix/scripts/bootstrap.py rollback
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
python3 skills/video-remix/scripts/bootstrap.py ensure --project /path/to/project
python3 skills/video-remix/scripts/bootstrap.py run --project /path/to/project -- status
python3 skills/video-remix/scripts/bootstrap.py project-upgrade --project /path/to/project
# 检查确认后显式升级：
python3 skills/video-remix/scripts/bootstrap.py project-upgrade --project /path/to/project --apply
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
  variants/<id>/
    spec.json           提示词、模型、输入、裂变父版本和改变项
    run.json            提交 ID、状态、费用、后端版本、人工选择
    raw.mp4             原始结果
  compare.html          条件与视频对照文档
```

通过 `replica / variation / rerun` 区分复刻、设计裂变和同条件重生成。已有版本不覆盖，新增版本关联父版本。

完整命令和版本 JSON 字段见 [工作指导](skills/video-remix/references/workflow.md)。根目录运行 `python3 -m video_remix --help` 查看入口。

`run` 默认输出执行预览，加 `--execute` 才真实提交。`--wait 30` 等待最多约 30 秒的轮询窗口（单次平台请求可能更长）；重复执行同一版本只恢复原任务，不会重新生成。

预算按本次选定版本的累计费用控制后续提交，无法强制平台限制单条实际扣费。超过单条预估就停止后续提交。提交结果不明时停在 `submit_intent`，核对平台后用 `attach` 绑定原任务。

## 开发与发布边界

```sh
python3 -m unittest discover -s tests -v
python3 -m video_remix doctor
```

测试使用临时 Git 仓库与假的平台响应验证更新、回退、预算和恢复；这些是内部软件测试，不是无密钥产品 demo，也不是成片质量证据。

本仓库不携带个人商品图、第三方视频、实验日志或登录凭据。公开示例素材需确认许可；用户自己的素材保存在仓库外。飞书导出由助手的飞书工具完成，后端不强制绑定飞书。

许可证：[MIT](LICENSE)。第三方模型、CLI 和输入素材分别适用其自己的使用条款。
