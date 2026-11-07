# 初始化、附加资源下载与 Python 旧版导入

确认日期：2026-11-02。本文件记录本轮确认的产品约定和后端接入设计，优先于 frontend-and-resource-plan.md 中较早的目录选择、硬链接选项与发布签名建议。前端和 Figma 不在本轮实现范围内。文末单独记录实际实现与验证范围，不以设计描述代替验收。

## 初始化

- 快速开始：释放三个内置角色的 Live2D、人设等非语音资源，配置 UP 内置连接，进入可选资源页，随后进入主页并展示大模型连接配置提示。
- 旧版导入：选择目录、扫描、确认内容、导入，补齐缺失的内置角色，进入可选资源页，随后进入主页。已有角色和用户修改不覆盖，不提供手动添加内置角色入口。
- 旧连接缺失、损坏或无法转换时保留可迁移数据，主页提示当前未配置可用连接，不自动替换为 UP 连接。
- 初始化完成、可选资源准备完成、提示已展示分别保存。可跳过可选下载，下载失败不妨碍已有文字功能，不重复执行已完成初始化。

## 前端仅展示三个组

| 功能组       | 选择单位                                    | 内部依赖                                             |
| ------------ | ------------------------------------------- | ---------------------------------------------------- |
| 语音识别模型 | 整组                                        | Whisper Small 多语言 q8 模型、ASR 运行依赖           |
| 语音合成模型 | 目前只有 GPT-SoVITS；描述为“让角色开口说话” | Python 环境、共享资源、v4 与 v2ProPlus 版本资源      |
| 角色语音     | 爱音、素世、祥子分别选择                    | 各自 GPT/SoVITS 权重、参考音频、文本、语言、形态映射 |

内部组件不成为前端独立选项。组内只处理缺失内容。角色语音依赖所选或已准备的 GSV；仅导入基础模型但缺少新环境时自动补选 GSV 组，明确显示补齐所需下载量。取消未准备的 GSV 时不能留下悬空角色语音选择。素世默认 soyo (62).wav，祥子黑白形态共用权重、分别使用参考材料。

## 平台、环境与发布

- GSV 环境仅覆盖 Windows x64 CPU、Windows x64 CUDA、macOS arm64。Windows 根据受验证的 GPU/驱动要求推荐 CUDA，否则 CPU，可改选 CPU。macOS 首先使用 CPU，MPS 完成验证后才启用。
- ASR 首版使用 CPU，仅需 Windows x64、macOS arm64 两种运行包。完整依赖在发布端安装、原生库准备和验证；客户端解压后由 Electron 的 Node 子进程按绝对入口加载，不运行 npm install。
- ASR 依赖从主应用生产依赖/打包产物中排除。开发环境可显式使用开发依赖，不允许正式应用静默回退到系统包。
- GSV 发布独立解释器、完整预编译 wheels、锁定依赖清单、私有 uv 和必要动态库。客户端在最终安装位置创建环境并离线安装，不访问临时包源、不编译、不修改全局 PATH。
- 虚拟环境创建后不搬动目录；候选安装使用独立安装身份，验证后切换启用记录。旧环境等实际进程退出后才能清理。云端不需要保留历史目录，本地允许为切换短暂共存。
- Python 服务源码仍随主程序发布。模型与环境分开管理，语音请求按角色权重选择 v4/v2ProPlus，不把单个 architecture 固定为整个服务唯一架构。
- NLTK、Open JTalk 等外部数据显式绑定资源库位置，推理期间禁止隐式联网获取模型。

固定资源源：

- Jerry4787/custom-gsv-model 数据集：character-voices/ 与 asr-whisper-small-q8/，均已发布 release.json。
- XXXXRT/GPT-SoVITS-Pretrained 模型仓库：共享资源及 v4/v2ProPlus；不下载其他架构专属权重或训练判别器。
- macOS arm64 的 ASR、GSV CPU 运行包已发布到同一数据集的 `runtimes/`；Windows 运行包尚待构建、发布和目标平台验收。未发布时明确不可用，不伪造大小或就绪状态。

采用 HTTPS 固定仓库/路径和 SHA-256，不额外实施公私钥签名。清单只描述相对文件路径、版本、大小、哈希、依赖及兼容条件，不能注入任意网站、本机路径或安装命令。允许经核验的 ModelScope CDN 重定向；不持久化临时 CDN 签名 URL，不向 CDN 转发上传密钥。ModelScope 上传密钥不进入客户端。

固定目录覆盖发布时先上传资源、最后提交声明。下载任务固定清单快照，哈希变化需要刷新并重算任务，不能将旧分片与新文件拼接。远端新版不兼容当前主程序时提示升级；已有本地版本继续可用。

## 旧版扫描与转换

仅支持 d_sakiko_config.json 和 reference_audio/all_conversation.json，不要求同时存在。识别到更早格式时提示先用新版 Python 程序打开并保存。损坏文件与不存在必须区分。扫描只读取数据，不执行旧程序及其自动迁移代码。

迁移角色身份、人设、头像、主题色、顺序、用户人设、完整 Live2D 包与形态/映射、角色权重和多情绪参考材料、已识别的完整 GSV 依赖、大模型连接及选中项、聊天及关联图片/附件/历史音频。旧版 GSV 完整迁移与新下载仅支持两类架构是不同规则。未被当前引擎支持的迁移资源保留但不自动启用。

不迁移旧 ASR、旧 Python 环境、世界书/embedding、缓存。参考材料兼容当前文本文件和 reference_audio_and_text.json。连接进行协议与已知前缀转换，密钥仅写入后端私有存储，无法自动转换的连接保留为待配置记录，不为了导入调用付费接口。

重复身份/同名角色静默跳过并记录来源映射。缺语音或 Live2D 不阻止保存角色资料；历史音频缺失不删除文本。所选目录之外的引用报告缺失/越界，不递归扫描任意位置。

## 硬链接与空间计划

只对 GPT_SoVITS 目录内明确不可变、至少 32 MiB 的模型权重默认尝试硬链接，不提供用户选项；角色定制权重、普通配置、Live2D、参考音频和聊天附件复制。已准备且源快照未改变的文件直接复用，重复角色不会再次复制角色资源。

扫描只读；生成可执行计划时在目标资源库未启用位置逐文件尝试建立硬链接，成功的链接保留至提交，不依靠同盘判断猜测。失败时计划明确改为复制。取消/过期清理候选链接，不修改源文件。正式执行重新确认候选与源文件状态，来源变化使相关计划失效。

分别提供逻辑大小、预计新增占用、各卷处理期间的空间需求。链接不重复计算内容大小；复制按保守大小计算，下载包、解包、环境安装候选及旧环境共存按生命周期计入。可用空间可随时变化，执行前重新检查。空间不足或实际读取失败要报告，静默降级不表示吞掉错误。

硬链接不能进入会 chmod、原地覆盖或截断文件的普通复制分支；更新创建新文件后切换引用。大文件准备不占用全局资源索引队列。

## 模块接入

- Onboarding：管理初始化路径、完成条件与提示状态；资源准备由 ResourceSetup 执行。
- ResourceSetup：清单解析、来源授权、扫描、计划、持久任务、下载/导入/环境安装及验证；内置、本地、远端共用登记和绑定。
- MediaAssets：继续统一模型/Live2D/音频引用，新增受控准备后登记，保留引用提交恢复规则。
- ResourceImports：继续用于普通编辑草稿，不承担跨进程的长任务租约。
- CharacterCatalog/Settings：使用原保存接口及 revision 校验，禁止直接改写业务 JSON。
- PythonChatImporter：将支持的 Python JSON 转换为对话草稿，复用 Conversations 的持久提交和来源去重，不生成伪造的工具执行或重新触发历史任务。
- SpeechResources/SpeechProcess：按启用环境解析解释器，固定进程资源快照，worker 按真实权重选择架构；旧进程退出后释放占用。
- Recognition：从安装记录获得依赖入口与模型目录，继续使用独立子进程。
- Electron 主进程：授权目录选择，返回来源句柄，不使用选择后立即复制整目录的 importFiles。后端不依赖 Electron。
- HTTP/WS：公开 onboarding/setup 的有限入口与完整任务快照，不开放绝对路径或任意安装命令。

应用数据保存安装记录、来源映射、计划/任务与初始化状态。资源库保存模型和环境，缓存保存可丢弃下载材料。解释器/依赖以完整环境管理，不为每个包文件分配媒体引用。

## 契约和恢复

前端入口：onboarding.getState/begin/finish/acknowledgeNotice；setup.getCatalog/scanLegacy/createPlan/start/getJob/controlJob。宿主另提供受限来源授权，订阅通过现有 WebSocket。

发布声明、安装记录、来源映射、操作计划、任务记录、初始化状态分别持久化。任务分阶段报告扫描、获取文件、校验、安装、提交、验证。以角色/聊天/组件为提交单位，成功项保留。计划到任务一一对应，跨重启重试不能重复导入。暂停/取消在安全节点生效；页面关闭不取消，应用退出保存恢复信息，下次由用户继续。

先登记持久 owner 的恢复读取能力，再恢复资源引用和任务，最后开放请求/后台维护。启动时清理旧临时草稿不能误删持久准备任务。退出先停止新任务与启用操作，再关闭业务和推理进程。

## 验收要求

验证快速开始和缺失内置角色补齐、当前格式扫描与更早格式提示、跨卷复制降级、硬链接不修改源权限、空间估计、源文件变化、断点续传、远端覆盖、校验失败、取消/重启恢复、提交中断去重、连接密钥不外泄、缺资源仍保留聊天，以及同一服务运行 v4/v2ProPlus。

真实发布包另需验证 Windows CPU/CUDA、macOS arm64 的干净环境安装、含空格/中文的安装路径、离线模型加载、实际 ASR 与 TTS、更新失败保留旧环境和主应用不携带 ASR 依赖。

## 实现状态

后端已经落地，前端和 Figma 未修改：

- `src/backend/onboarding/` 保存初始化状态，释放内置角色、补齐缺失角色、选择连接并提供提示状态。
- `src/backend/setup/` 提供固定来源清单、扫描和确认计划、持久任务、分片续传、SHA-256 校验、安全解包、离线运行环境安装、组件启用及进程租约。
- `resources/builtin-characters/` 包含三个角色的人设与四套新 Live2D v3 模型，按角色压成三份 tar.gz，模型归档合计约 22.63 MiB，不含语音权重。启动异步优先解压白祥，正式默认仍为黑祥（无面具演出服）；清单保留 `version: 1`，用独立 `contentVersion` 控制预览缓存，不做整包哈希校验。格式与打包命令见 [内置角色资源说明](builtin-characters.md)。`resources/setup/` 保存已核验发布声明与 GSV 文件清单。
- 当前 Python 资料转换为现有 CharacterCatalog、Settings、ChatStore 数据，仍通过它们的引用提交与版本校验保存。聊天使用旧 `chat_id` 派生稳定身份；移动旧版目录后再次导入也不复制聊天。
- 角色缺少模型/语音不阻止迁移。旧模型选择、形态、小剧场情景、用户人设、附件和可读 WAV 历史音频可迁移；未知历史形态保持未知，不猜测黑白祥。无法映射到完整轮次末尾的摘要只保留为迁移资料，不改变上下文截断位置。旧工具执行不重新触发，原始转换材料保留在后端私有扫描/任务记录。
- GSV 支持多架构启动快照，worker 根据真实权重选择资源；原有固定架构调用仍兼容。NLTK 禁止隐式下载，词典及文本缓存使用显式位置；用户日语词典编译结果写入 worker 缓存，不修改应用源码。
- ASR 依赖已移为开发依赖，生产工作进程仅动态加载安装记录中的绝对入口。macOS 增加外部原生库加载所需的签名 entitlement，仍需真实签名包验收。
- 内置 UP 连接使用发布配置注入，不从用户选择的旧 Python 程序执行代码提取密钥。发布配置缺失时返回 `model-connection-missing`。

刷新 `runtimes/release.json` 后，macOS arm64 的两个运行环境包可用；Windows 的三个运行环境包尚未发布，相应下载选项显示不可用，任务不能跳过该依赖后声称整组就绪。Windows CUDA 目前由调用方明确选择，安装时检查 CUDA 可用性；在运行包提供明确驱动约束并经过目标机验证前，默认使用 CPU。

## 后端调用顺序

所有调用继续使用现有 `/call/<namespace>/<method>` 传输、鉴权和参数数组格式。没有提供任意本机路径、任意下载 URL 或任意安装命令的 HTTP 接口。

1. 调用 `onboarding.getState()`。快速开始使用 `onboarding.begin({ path: 'quick' })`。
2. 旧版导入先通过 preload 的 `selectLegacyDirectory()` 获得 `{ sourceId, name }`，再调用 `setup.scanLegacy(sourceId)`。扫描结果包含 `scanId`、角色摘要、聊天数、GSV 文件数和告警，不含密钥或绝对目录。
3. `setup.createPlan({ kind: 'legacy', scanId, include: { characters: true, connections: true, chats: true, gsv: true } })` 返回计划快照。省略 `include` 表示全部导入。确认后调用 `onboarding.begin({ path: 'legacy', planId })`。
4. 通过 `setup.getJob(id)` 或 WS `topic: 'setup', key: id` 观察任务。重连/重启可调用 `setup.listJobs()`。`setup.controlJob(id, 'pause' | 'resume' | 'cancel')` 控制任务。任务逐项提交，暂停不会回滚已完成的内容；任务失败后可继续或重新规划。
5. 导入成功后调用 `onboarding.prepareOptionalResources()`，补齐内置角色并进入下载阶段。
6. `setup.getCatalog({ refresh: true, accelerator: 'cpu' })` 刷新固定来源声明。首版只接受 `asr`、`gsv`、`voice.anon`、`voice.soyo`、`voice.sakiko` 五个产品选择。返回三个组；`sizeKnown: false` 表示不能将已知部分下载量当作完整大小。
7. `setup.createPlan({ kind: 'download', selection: ['asr', 'gsv', 'voice.anon'], accelerator: 'cpu' })` 自动展开依赖，只准备缺失内容。角色包会自动补齐 GSV 依赖；存在完整自定义角色语音时直接复用。确认后调用 `setup.start(plan.id)`。
8. 完成或跳过下载后调用 `onboarding.finish()`；进入主页后展示 `notice`，展示确认后调用 `onboarding.acknowledgeNotice()`。初始化也可订阅 WS `topic: 'onboarding'`。

`space.logical` 是选中本地内容/待准备组件的逻辑大小；`space.added` 是预计新增资源占用；`space.download` 与 `space.cache` 分别表示下载量和下载缓存预算。`volumes` 按实际文件系统合并执行时需求，含解包、环境安装和音频交接的峰值，并扣除已完成内容。硬链接和已复用文件不重复计算内容大小。计划有效期 24 小时，未完成的规划在重启时清理；已暂停任务不按计划有效期删除。

## 发布端需要准备的文件

角色语音和 ASR 模型沿用当前数据集中的 `character-voices/release.json` 与 `asr-whisper-small-q8/release.json`，不增加 revision 目录。下载时固定所用声明，覆盖发布造成哈希变化时必须刷新再规划。程序自带一份已核验声明，离线可以查看目录；刷新失败不会替换该声明。

运行环境统一放入同一数据集的 `runtimes/`。每个平台生成一个 ZIP，**归档根目录直接包含下列内容，不额外套一层包名目录**，不要使用符号链接。构建机安装、检查依赖并生成清单；客户端不从 npm、PyPI 或 GitHub 安装软件。

| 环境                           | ZIP 内必要内容                                                                                                                                                                                                                         |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ASR，Windows x64 / macOS arm64 | 完整锁定的 `node_modules/`、依赖锁定记录及许可证；入口必须为 `node_modules/@huggingface/transformers/dist/transformers.node.mjs`。包含 Transformers 3.8.1、ONNX Runtime Node 1.21.0 及实际全部传递依赖。原生安装脚本在构建机运行完成。 |
| GSV，Windows x64 CPU / CUDA    | `python/python.exe` 及完整独立解释器文件、`uv/uv.exe`、`bin/ffmpeg.exe` 及配套动态库、`wheelhouse/`、`requirements.lock` 和许可证。                                                                                                    |
| GSV，macOS arm64 CPU           | `python/bin/python3` 及完整独立解释器文件、`uv/uv`、`bin/ffmpeg` 及配套动态库、`wheelhouse/`、`requirements.lock` 和许可证。                                                                                                           |

GSV 建议统一采用 Python 3.11。根据 `python/pyproject.toml` 和 `python/uv.lock` 为每个目标平台准备完整 wheels；CPU/CUDA 使用对应 Torch 构建。`requirements.lock` 必须固定全部传递依赖并包含每个 wheel 的 SHA-256。客户端先在资源库的最终安装身份下执行私有 uv 创建环境，再通过 `--offline`、`--no-index`、`--require-hashes`、`--only-binary :all:` 安装。独立 FFmpeg 仅加入语音子进程的 PATH。验证通过后切换安装记录，旧进程继续使用原环境；只有确认不再被进程占用后才清理旧环境。

`runtimes/release.json` 格式如下（大小与哈希必须从真实归档生成，下面的占位值不能用于发布）：

```json
{
  "schemaVersion": 1,
  "packages": [
    {
      "kind": "asr",
      "target": "darwin-arm64",
      "version": "1.0.0",
      "setupApiVersion": 1,
      "installBytes": 0,
      "files": [
        {
          "path": "asr-darwin-arm64.zip",
          "sizeBytes": 123,
          "sha256": "归档的 64 位 SHA-256",
          "archive": "zip",
          "expandedBytes": 456
        }
      ]
    }
  ]
}
```

ASR 的 target 为 `win32-x64` 或 `darwin-arm64`；GSV 为 `win32-x64-cpu`、`win32-x64-cuda` 或 `darwin-arm64-cpu`。`expandedBytes` 是解压总大小上限，`installBytes` 是创建最终环境额外需要的磁盘预算，ASR 无额外安装步骤时为 0。当前安装器版本为 `setupApiVersion: 1`；较新安装协议会提示升级主程序。归档不能自带命令模板或任意来源地址。

UP 默认连接在发布机器设置受保护的 `DSAKIKO_UP_API_KEY`，运行 `node scripts/prepare-builtin-connection.mjs` 生成 `resources/builtin-connection.json`。该文件被 Git 忽略，但会随应用资源发布；不要把上传 ModelScope 的密钥用于此配置。开发测试使用注入的测试连接，没有调用付费接口。

## 本轮验证与边界

- 后端 Vitest：25 个测试文件、195 项测试通过；ESLint 与 `git diff --check` 通过；Electron Vite 构建通过。Python 使用项目 `.venv` 执行 36 项测试，35 项通过，1 项真实大模型测试按条件跳过。尚未验证全新依赖安装与正式平台打包。
- 新增后端测试覆盖初始化、缺失角色补齐、保留旧人设、导入/聊天去重、硬链接成功和跨卷降级、空间预算、源文件变化、损坏/早期格式、链接越界、ZIP 解包约束、下载续传/哈希失败/跳转限制、跨重启继续、提交中断恢复及运行环境租约。
- 对当前本地 Python 目录进行只读扫描，识别到 7 个角色、23 段聊天、42 个 GSV 资源文件；未向用户现有 Electron 数据目录实际导入。
- Python 测试覆盖 v4/v2ProPlus 资源并存及单组缺失，以及既有调度/进程生命周期。真实大模型端到端推理测试需要另外指定测试清单。
- Windows CPU/CUDA、macOS arm64 的全新环境安装、实际 ASR/TTS、签名/公证后的外置原生库加载仍属于运行包发布验收。当前开发机通过测试不代表这些发布验收已完成。
- 初始化后端实现阶段没有制作或上传运行环境包，没有修改前端或 Figma，也没有改造 Windows 安装器的数据目录布局。后续运行包构建工具与验收状态见 [运行环境包构建](runtime-package-build.md)。
