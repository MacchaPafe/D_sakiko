# 后端实现状态与调用方式

2026-10-26 补充：内置工具拆分并扩展为 13 项，新增对话 workspace、工作文件下载与持久抽签。调用与限制见[内置工具与对话工作目录](backend-tools.md)。

状态复核：2026-10-28。设计依据仍是 `core-interfaces.md`、`python-core-design-review.md` 和 `python-feature-requirements.md`。本文件记录实际实现边界，不能把接口声明、构建成功或旧原型测试等同于全部需求验收。本次已接入本地 ASR、模型目录缓存、历史裁剪与工具权限分离；运行了后端自动测试及真实 Whisper 中文样例，尚未进行 GUI、发布安装包或付费 LLM 验收。

2026-10-28 已实现本次四项改进：Transformers.js + Whisper ONNX 本地识别、长上下文完整旧 Turn 裁剪、Models.dev 上限查询与持久缓存、固定工具目录与逐轮执行权限分离。其他审查缺口继续暂缓；具体支持范围与验证限制见下文。

2026-10-25 补充：LLM 接入已支持 Completions、Responses、Anthropic Messages 和 Gemini generateContent，增加多连接预设、协议元数据存档与按服务区分的推理回传策略。接口、限制及单次 DeepSeek 实测记录见 [LLM 连接与推理回传](llm-providers.md)。

## 入口与分层

正式装配入口为 [create-core-backend.js](../src/backend/create-core-backend.js)。业务代码写在原有 `src/backend/*/interface.js` 中，构造函数接收能力，返回值保持普通数据。旧装配入口、扁平对话与业务实现、演出 IPC 代理及对应脚本已经删除。公共文件工具迁入 `backend/core/files.js`，Python 传输整理为 `speech/python-transport.js`，通用错误位于 `shared/problems.js`；正式后端不再依赖旧桌面协议。

- 后端 Performance 拥有所有 sequence 的计划时钟、队列、guide 终态和模型计划目标；没有前台选择，没有客户端播放回报接口。
- Conversations 拥有 Chat、Turn、保存状态、受理门禁、async 组和提醒。查询与订阅交付同一份已发布快照，避免读取一次修改的中间状态。
- HTTP/WS 只映射白名单业务入口；凭据解析、资源本机路径、工具执行、提醒投递、交互资格和底层队列写接口不公开。
- 前端 `backend/client.js` 是框架无关的传输封装；`performance/interface.js` 是本地播放控制器。React 组件、舞台布局和 Live2D SDK 挂载留待 UI 阶段。
- Electron IPC 只提供 `dsakikoHost.getBackendConnection()`、运行信息和文件选择导入。Python 进程由主进程管理，随应用退出关闭。

宿主分别注入 `appDataRoot`、`resourceLibraryRoot` 和 `cacheRoot`。应用数据保存设置、聊天文本、图片与资源登记，资源库保存模型及音频，缓存保存临时材料、日志和 Chromium 会话数据。三个环境变量分别为 `DSAKIKO_DATA_DIR`、`DSAKIKO_RESOURCE_LIBRARY_DIR`、`DSAKIKO_CACHE_DIR`；Python 解释器仍使用 `DSAKIKO_PYTHON`。旧 `DSAKIKO_RESOURCE_ROOT` 已停用。具体布局与兼容限制见[数据目录设计](data-directories-and-resources.md)。

## 已实现

| 模块                        | 当前行为                                                                                                                                                                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Settings / CharacterCatalog | JSON 持久化、版本比较、生成/语音选项校验、连接私有凭据、形态与模型选择、人格快照。凭据文件采用仅当前用户读写权限；尚未接系统钥匙串。                                                                                                                          |
| ChatStore / MediaAssets     | 不可变 Chat 版本文件与原子索引提交、提醒 ID 唯一性、摘要排序、损坏检测及保留原件恢复、JSON 资源归档；资源导入、引用集合和受控读取。                                                                                                                           |
| Conversations               | 嵌套历史、先持久化再受理、运行中保存故障保留内存、retrySave 不重跑、停止与当前句门禁、末轮重生成、历史编辑/截断/分支、摘要、音频重合成、提醒扫描。                                                                                                            |
| Agent                       | AI SDK 非流式请求，支持 Chat Completions、Responses、Anthropic Messages、Gemini generateContent；固定 Nunjucks 模板、响应分组、工具串行执行、有限格式修复、上下文估算与摘要、协议元数据存档及推理回传。图片可使用内联或厂商 Files API；非图片进入 workspace。 |
| Tools / Interactions        | 13 项内置工具、按对话启停与依赖闭包、宿主能力检查、sync/async 两模式；时间、硬件、天气、Bocha 搜索、文件、换装、提醒、抽签与问答。抽签持久化并由后端抽取；问答支持自由回答和跳过，普通问答待处理状态仍不跨重启恢复。                                          |
| 后端 Performance            | 无客户端也推进；未 ready 的队首不跳过；多 sequence 独立；停止保留当前句；等待者取消不取消任务；目标状态动作排队、幂等与抢占处理。                                                                                                                             |
| Speech / Python             | 复用 Python 调度器，固定语言/声音/采样值、共享结果导入、有限通信重试；可信资源根与应用资源目录隔离检查，启动精度和结构配置传入 worker。                                                                                                                       |
| HTTP / WS                   | 仅监听 127.0.0.1；Bearer、Host/Origin 检查；单次短期 WS 票据；完整快照订阅；慢客户端断开后重新同步；短期资源地址与音频 Range 请求。                                                                                                                           |
| 前端封装                    | 分模块 Promise 方法、订阅重连、可供 useSyncExternalStore 使用的快照容器、本地总/BGM 音量、独立回放、受理成功后恢复实时、无 UI 的音频输出适配器。                                                                                                              |

后端模型状态是**计划目标**，不是任何客户端实际模型实例的确认值。具体渲染器须根据最新目标实现本地状态，不向后端报告“播放完成”。逐字进度由客户端按当前 guide 的 elapsedMs、durationMs 和本地单调时钟估算，不逐字请求后端。

保存版本采用单个后端进程作为唯一写者；不支持两个进程同时打开同一组数据目录。聊天只保留当前已提交 revision：写完新版本并发布索引后清理旧文件，启动时清理中断残留；损坏恢复的 `.backup` 保留。持久引用由存储模块统一交接，运行使用由临时 owner 或租约保护，具体见[资源生命周期](data-directories-and-resources.md#引用生命周期与聊天版本清理)。`collectUnused` 已支持显式即时回收、预览与限定 ID；单项删除失败继续，索引提交失败停止并隔离受影响 ID。启动仅清除零引用缺失记录。桌面入口在后端启动前限制单实例，重复启动唤醒已有窗口。基础模型使用逐文件版本清单，由设置及运行进程持有。迎新、硬链接导入、清理 UI、目录迁移、容量查看、配额和资源自动回收暂缓。

## Python 功能对照与下一阶段范围

“已有实现”指存在正式业务路径，不代表供应商、设备和模型效果均已验收。需求编号沿用 [Python 功能清单](python-feature-requirements.md)。

| 能力 / 需求                                                                | 当前源码状态                                                                                                                                                                                    | 下一阶段范围                                                |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| ASR（INPUT-03～06）                                                        | Recognition 已装配到核心后端，提供 prepare/transcribe/getState/cancel；Whisper Small q8、CPU 独立进程、本地 WAV、取消与空闲释放。                                                               | 已实现后端；录音 UI、草稿管理、发布资源下载仍不在本次范围。 |
| 上下文裁剪兜底（AGENT-07、08）                                             | 自动摘要失败或仍超限时裁剪最旧完整 Turn；保留摘要与存档，每步生成前重新检查。                                                                                                                   | 已实现；当前工具链超限时明确失败，不自动重跑。              |
| 模型上下文上限查询（AGENT-02、07）                                         | Models.dev 精确映射、三天缓存、后台刷新、失败退避与旧值复用；手工上限优先，已知窗口覆盖兜底预算。                                                                                               | 已实现；token 仍为字节估算，附件／不透明推理保持 unknown。  |
| 世界书运行时（WORLD-01～05、08）                                           | 配置与角色映射有承接位置，但 [Worldbook](../src/backend/worldbook/interface.js) 与查询绑定为空；开启后生成报 worldbook_adapter_required。缺少范围解析、权限过滤、直接注入、查询工具及诊断交付。 | 暂缓。                                                      |
| 语音业务文本处理（TTS-03、WORLD-06）                                       | 当前生成台词直接成为 spoken.text，pronunciationOverrides 为空；传输层已有替换能力，但未接入 Python 的人名／术语读音与朗读文本清洗。底层 GPT-SoVITS 文本处理不等于这些业务规则。                 | 暂缓。                                                      |
| 完整 Live2D 导入（ASSET-05）                                               | 可复制整目录并解析模型能力；[Live2dImporter](../src/backend/live2d-import/interface.js) 为空，未实现依赖整理、引用重写及模型配置生成。                                                          | 暂缓。                                                      |
| Python 旧数据迁移（CHAT-08、09）                                           | 新版归档导入导出已有；[PythonChatImporter](../src/backend/storage/python-import-interface.js) 为空，旧角色目录、参考音频配置也没有整体迁移适配。                                                | 暂缓。                                                      |
| 语音预加载／空闲卸载（TTS-07）                                             | 有并发、驻留上限和换模型淘汰；没有切角色预加载、持续关闭语音或桌宠空闲时主动卸载。                                                                                                              | 暂缓。                                                      |
| 桌宠、WebUI、反馈、更新修复（APP-01～05）                                  | 只有主窗口和本机传输；关闭主窗口触发退出。未接入正式桌宠／托盘、远端配对接管、反馈或更新修复服务。                                                                                              | 暂缓，保留独立模块边界。                                    |
| Live2D 下载／编辑、世界书管理与制作（LIVE-09、ASSET-06、WORLD-07、APP-06） | 运行时读取演出目录不等于具备资源编辑器；相关管理工具未迁移。                                                                                                                                    | 暂缓；世界书制作继续作为独立开发工具，不进入发布程序。      |

普通聊天、双角色小剧场、历史操作、多协议 LLM、工具循环、角色形态和人格、语音调度、演出计划与资源生命周期已有正式实现。单消息删除、自由编辑提示词模板等旧行为已有新设计取舍，不列为遗漏。Python 文件工具本身只读取纯文本／代码，因此 PDF、Word、PPT 解析也不列为 Python 已有而 Electron 缺失的功能。

### ASR 本地实现（2026-10-28）

使用 @huggingface/transformers 3.8.1、Whisper Small 多语言 q8 ONNX 和 CPU（4 个推理线程），在独立 Node 子进程运行。开发模型已下载到项目忽略目录 .local/models/whisper-small，来源为 onnx-community/whisper-small，固定版本 36050c46d777d46dc4b5f43f6d90574fc38f8732。模型文件不提交 Git，也不打入安装包。开发宿主默认读取该目录，可用 DSAKIKO_ASR_MODEL 指定绝对目录；核心后端可通过 recognitionModelDirectory 注入。发布环境模型获取由后续资源下载模块设计，当前运行时禁止自动联网获取模型。

公开接口为 recognition.prepare()、transcribe({ audio, sampleRateHz, language, requestId })、getState()、cancel(requestId)。audio 必须是已登记的 AssetRef，接口不接受任意本机路径。支持 0.1～300 秒、最多 32 MiB 的 WAV，声明采样率须与文件头一致；混音与重采样在工作进程转换成 16 kHz 单声道 Float32。暂不支持直接输入 WebM/Opus；后续录音客户端应输出 WAV 或独立转换。HTTP 上传仍受现有 8 MiB 请求体限制，32 MiB 是核心识别接口限制。

同一时刻只运行一个识别任务；prepare 可提前加载，getState 可轮询加载进度与当前 requestId。cancel 只取消身份匹配的任务，终止实际进程并忽略迟到响应；缺模型、坏音频、超时和进程崩溃返回明确错误。模型空闲 5 分钟释放，应用关闭等待取消与音频引用释放。识别结果只是候选文本，不自动发送或修改草稿；取消 HTTP 等待不自动停止推理，客户端需显式调用 cancel。语言规范化与 INPUT-07 的繁转简继续暂缓。

开发机实测：macOS 合成中文 WAV“你好，今天下午三点提醒我喝水。我们正在测试本地语音识别。”；冷启动识别约 2.3 秒，驻留后约 1.3 秒。输出中的“语音”被识别成“余音”，仅证明链路可用，不代表中文准确率验收。原始记录位于忽略目录 .local/asr-validation.json。

依据：[Transformers.js Node 音频指南](https://huggingface.co/docs/transformers.js/guides/node-audio-processing)、[Whisper Small ONNX](https://huggingface.co/onnx-community/whisper-small)。

### 模型上下文上限查询方式核实（2026-10-28）

本项目安装的 ai 7.0.130 所用 LanguageModelV4 没有通用上下文上限字段或查询方法。gateway.getAvailableModels() 是 Vercel Gateway 的目录发现能力；本地安装版本返回的类型与映射不包含上下文上限，不能直接当作任意连接的上限查询。

Models.dev 是社区维护的开源模型元数据目录，提供网站和公开 JSON HTTP API；本项目可用 Node fetch 获取[公开 JSON 目录](https://models.dev/api.json)后在本地查询，无需安装专用 JavaScript 包，也无需发送聊天内容或模型 API 密钥。它不替当前模型端点探测能力。可采用其公开 JSON 目录，按供应商和精确模型 ID 查询 limit.context、可选 limit.input、limit.output。另一个来源是 Vercel AI Gateway 的公开 GET /v1/models，官方文档说明其返回 context window，但该值描述 Gateway 目录，不能保证适用于用户自定义代理。查询元数据不要求把模型生成改走 Gateway。

目录通过 ModelCatalog 解析，持久文件为 cacheRoot/model-catalog/models-dev.json，仅保留供应商、精确模型 ID 和上下文／输入／输出限制。官方预设地址可按精确 ID 自动查找；代理地址、自定义部署名需在 ModelDefinition.catalogMapping 中明确指定 { provider, model }，不做模糊匹配。Settings.resolveConnection 将结果合并进本轮模型能力快照，并记录来源和获取时间；无需额外的密钥。

缓存新鲜期为 72 小时；命中新鲜缓存不联网，过期立即使用旧值并后台刷新。刷新失败仍把旧条目视为有效上限，退避从一分钟逐次增长，最长按天限制；新鲜期不等于数据失效期限。首次无缓存时后台请求，当前对话不等待网络。刷新请求合并；下载、字段校验和原子写盘成功后才替换旧缓存。结构损坏、网络错误或未知模型都不阻塞文字聊天。

预算优先级为手工 overrides、declared、适用的目录条目、未知时的 contextTokenBudget（默认 300000）。已知更大的窗口不会被默认预算限制。默认 compression.thresholdTokens 改为 270000，实际按有效总预算的 90% 触发；旧默认 258000 在读取／受理时迁移，其他自定义阈值保留为绝对值。maxOutputTokens 按已知输出上限收窄，输入预算再扣除输出预留并遵守独立输入上限。摘要阈值不会超过可用输入预算。

模型上限未知时仍按兜底预算与摘要阈值工作，但 modelLimit 保持 null，不伪称模型实际支持 300000。附件或不透明推理令 token 估算为 unknown 时允许请求。当前轮次冻结模型能力，不在工具循环中因刷新改变预算；远端拒绝超长请求时保留执行记录，不自动重跑工具。

依据：[Models.dev API 与字段](https://github.com/anomalyco/models.dev)、[AI SDK Gateway 目录发现](https://ai-sdk.dev/providers/ai-sdk-providers/ai-gateway#dynamic-model-discovery)、[Gateway 模型元数据 REST API](https://vercel.com/docs/ai-gateway/models-and-providers)。本次已接入目录服务；此前已验证绑定物理网卡的无代理下载成功。线上目录仍属于元数据来源，不是实际模型端点容量探测。

## 请求上下文组成与裁剪边界（2026-10-28）

本节记录当前请求组成与已实现的裁剪边界。入口为 [Conversations.prepare/history](../src/backend/conversation/interface.js)、[Agent.messages/runPrepared/compress](../src/backend/llm/interface.js) 和 [协议回传](../src/backend/llm/replay.js)。

### 普通生成请求的组成

Agent 向 AI SDK 传入独立的 system、messages 和 tools，SDK 再转换为各供应商的报文。以下是 SDK 层的语义顺序，不保证各协议使用相同的 HTTP 字段名。

| 组成                                       | 实际内容与来源                                                                                                                                                                          | 当前是否随历史累计                                             |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| system                                     | 固定 system.njk：角色扮演原则、上下文标签约定、工具使用和最终 JSON 输出要求。不包含具体角色描述。                                                                                       | 每次发送同一份。                                               |
| messages 中的人物资料 user 消息            | character_context：参与角色的名称、全部形态的名称与描述、用户人格名称与描述。先按历史 Turn 的人物快照恢复；与上一次资料不同才插入更新，当前资料也会在本轮输入前核对并补入。             | 只保留必要的版本变化；仅切形态且完整人物资料没变时不重复插入。 |
| messages 中的历史                          | 有效累计摘要，以及之后的完整 Turn；没有摘要时为全部历史。user 包括手动输入、提醒或异步结果；assistant 包括台词、工具调用和符合回传规则的推理／协议块；tool 包括调用身份与完整 outcome。 | 是，受摘要投影和协议有效性限制。                               |
| messages 中的本轮输入                      | 手动输入文字与图片；或带 scheduled_reminder / async_tool_results 标签的内部输入；小剧场无手动文本时为“请按当前情景开始角色对话。”。                                                     | 受理后保存业务输入；小剧场启动提示在请求构建时生成。           |
| messages 末尾的 runtime_controls user 消息 | 当前模式、参与角色当前形态、V3 动作／表情／组合目录、小剧场情境与风格、补充背景、语言与翻译、情绪枚举、JSON 字段及示例。                                                                | 不保存为历史消息；每轮重新生成。                               |
| tools                                      | 全部内置工具的名称、说明及 JSON 参数 Schema，独立于消息数组。工具执行函数不发送给模型。                                                                                                 | 每步发送本轮固定集合，不逐轮叠加旧定义。                       |
| 请求参数                                   | 模型、输出上限、推理设置、采样参数、超时等；不是普通聊天消息。                                                                                                                          | 本轮固定。输出预留参与预算，超时等参数本身不作为文本计数。     |

人物资料未变化、已有累计摘要时，首个模型请求可表示为：

```text
system: 固定扮演与协议要求
messages:
  user: character_context（摘要所在历史位置的人物快照）
  user: 历史累计摘要 S
  [历史 Turn A: user → assistant(tool-call) → tool → assistant(dialogue)]
  [历史 Turn B: user → assistant(dialogue)]
  user: 本轮输入
  user: runtime_controls
tools: 固定完整工具定义（是否允许执行由 runtime_controls 指定）
```

若历史或当前人物资料变化，会在对应位置追加完整 character_context；它不是每个 Turn 固定占一条消息，也不一定只出现一次。普通业务投影的历史台词带正文、翻译、情绪和非默认演出选择，不带音频、播放进度及模型文件内容；有效原始回传则保留允许的原始 assistant 内容。

图片可以内联或使用远端引用，历史图片也会重新参与请求准备。非图片只提供工作文件名称／引用，文件内容通过工具结果进入上下文。runtime_controls 中“本轮补充知识”目前实际是 workspace 使用说明和当前工作文件名列表，世界书尚未接入。旧 Turn 被裁剪不等于 workspace 文件消失，当前文件列表仍会存在。

### 摘要与本轮追加内容

history(chat) 遇到 abstract 时，会清空此前已投影的历史及该 Turn 中摘要之前的消息。因此正常请求只使用最后一份有效累计摘要及其后文；原存档不删除。摘要以 user 消息发送，当前内部仍放在携带它的 ContextTurn 中，不能用 history.shift() 误把摘要视为最旧聊天轮次删掉。

自动摘要发生在 Turn 受理前。候选范围为旧历史减去 retainedTurns（默认 2，至少 1 个 Turn）；摘要调用有独立的 summary.njk system，输入为旧摘要与新增历史的纯业务 JSON，包含人物快照、文字、工具参数／结果和附件引用，不含明文 reasoning、签名／密文或附件原始内容。摘要结果写到覆盖末 Turn 的末尾，然后重建普通生成上下文。摘要请求先检查自身输入预算，已知超限则进入普通历史裁剪，不分块再次摘要。

首个模型响应后，runPrepared 会继续在同一消息数组追加：

- assistant 原始响应，包括文字、reasoning、签名等允许的协议材料和 tool-call；同一响应不拆散。
- 对应的 tool 结果，同一响应的所有调用结果在该步执行后合并追加。
- 若无工具调用但台词格式错误，追加要求修复 JSON 的 user 消息；保留此前工具结果，不从头重新执行。

受理时检查并裁剪初始上下文，循环内每次 generate 前重新检查增长后的工具结果和修复消息；超限则停止，保留已执行记录。未知附件／推理成本使 inspectContext 返回 unknown，目前不会因此拒绝生成。

### 已实现的移除规则（2026-10-28）

1. 原存档保持完整，只调整本次请求投影。先使用已有摘要；按配置尝试累计摘要，摘要失败后才进入裁剪兜底，不能把失败结果写成有效摘要。
2. 将累计摘要与可移除的普通历史 Turn 分开。从最旧的普通 Turn 开始移除整个 Turn，包括其输入、所有 assistant、tool-call 与 tool-result，保持剩余历史为连续后缀；不按单条 SDK 消息或字符截断。
3. 系统要求、当前人物资料、本轮输入、本轮控制、工具定义和本轮已经执行的工具链属于受保护内容。人物资料由保留的 Turn 快照重新投影，而不是直接删除扁平消息数组，避免只剩台词而丢了当时的人物资料。
4. 累计摘要默认保留，近期 retainedTurns 是摘要策略的保留数量，不承诺紧急裁剪时全部保留。普通历史全部移除后仍超限，则明确失败；是否进一步舍弃／缩短摘要需要单独决定，不在兜底中静默执行。
5. 裁剪改变历史前缀，会使后续历史响应的 contextFingerprint 不再匹配。必须重新经过 restoreReplay，按现有规则退回业务投影，不能原样带回失效签名或密文。存档中的原始协议材料仍保留。
6. 首版在第一次模型调用前确定历史范围，并在每步调用前检查新增材料。工具循环已开始后不能直接换历史前缀，尤其不能破坏前缀绑定的推理协议；若受保护的本轮工具链使请求超限，先明确失败并保留执行记录，不自动重跑整轮。循环中如何安全再次压缩是另一个需要协议适配的策略。

2026-10-28 验证：完整 JS 测试 24 个文件、181 项通过；变更范围 ESLint、构建及 git diff --check 通过。新增目录缓存／断网／精确映射、完整 Turn 裁剪与摘要保护、禁用调用零副作用、工具循环增长超限、ASR 音频预处理与真实子进程取消／崩溃／空闲释放测试。Electron 44.9.3 自带运行时成功启动构建后的识别工作进程，同一中文样例耗时约 2.4 秒；验证记录为 .local/asr-electron-validation.json。生成与工具测试使用本地模拟模型服务；未测试付费 LLM 的真实窗口容量或缓存账单。

## 前端调用示例

下面只展示未来页面如何使用封装，本次没有接入现有 React 原型：

```js
import { createBackendClient, watchConversation } from './backend/client.js'
import { Performance } from './performance/interface.js'
import { createAudioOutput } from './performance/audio-output.js'

const connection = await window.dsakikoHost.getBackendConnection()
const client = createBackendClient(connection)
const playback = new Performance({ client, output: createAudioOutput(client) })
const chatStore = watchConversation(client, chatId)

// 后续组件可使用 useSyncExternalStore(chatStore.subscribe, chatStore.getSnapshot)。
const snapshot = await client.conversations.getSnapshot(chatId)
playback.follow(snapshot.presentation.sequenceId)

// requestId 在同一次业务发送重试时复用。只有受理成功才退出本地回放。
const requestId = crypto.randomUUID()
await playback.createTurn(chatId, { text: '你好', attachments: [] }, { requestId })

const materials = await client.conversations.getReplayMaterials(chatId, {
  kind: 'turn',
  turnId
})
playback.replay(materials, { silent: false })
playback.setPlaybackOptions({ volume: 0.8, bgmVolume: 0.4 })

// 回放不因其他客户端的新消息而中断，用户可主动恢复。
playback.resumeLive()
chatStore.dispose()
playback.close()
client.close()
```

WebUI 使用同一个 `createBackendClient({ baseUrl, token })`，不依赖 `window.dsakikoHost`。当前尚未实现浏览器登录/配对页面；可信宿主须提供 token，并将实际 WebUI Origin 加入服务白名单。不得把永久 token 放在查询参数或永久资源引用里。

直接调用通常不需要关心传输身份。需要恢复一个结果不确定的非 Turn 命令时，显式保存 `call` 的 requestId：

```js
await client.call(
  'interactions',
  'complete',
  [{ requestId: interactionId, status: 'succeeded', value: 'A' }],
  { requestId: commandId, signal }
)
```

这里 `commandId` 是传输命令身份；`interactionId` 是问题身份，两者用途不同。HTTP 写命令缓存同 ID/材料的结果，异材料拒绝；缓存仅在一次后端运行期内有效，上限 10000 条，满后明确拒绝，不悄悄淘汰后重放副作用。Turn 另有随 Chat 保存的受理记录，重启、重生成或截断历史后相同业务请求也不重新执行。取消 fetch/订阅只取消客户端等待，停止业务须显式 `stopTurn`。

上传字节使用 `client.assets.upload({ name, mediaType, base64, purpose })`，请求 JSON 上限 8 MiB。`purpose` 必须是已支持的用途，如 `chat-image`、`avatar`、`audio` 或 `model`；不接受目录类别或本机路径。大权重使用 `dsakikoHost.importFiles({ purpose: 'model' })`。模型目录可使用 `{ purpose: 'live2d', directory: true, entry: 'model.model3.json' }` 整包复制，保留包内相对结构；这不包含 ZIP 解包与角色模型配置生成。读取资源使用 `client.assets.access(asset)`，当前地址有效 5 分钟，不持久化 URL。模型包成员可在该地址附加 `file` 查询参数，值为经 URL 编码的包内相对路径；后端校验实际路径仍在包内。

## 尚未接入的模块与验收边界

- 厂商 Files API 已通过 AI SDK 接入后端，支持自动选择、远端引用复用和 DeepSeek 删除，详见[远端文件后端说明](remote-files.md)。世界书真实检索与知识权限过滤、Python 旧存档/模型包导入适配仍未实现。启用世界书或选择本轮不支持的附件方案会明确拒绝，不伪装成已检索或已上传。
- Live2D 实际渲染、桌宠/托盘窗口切换、输入草稿/录音组件、WebUI 配对与页面均不在这次 UI 之外的首批实现里；已提供播放输出接点，尚未证明实际模型动作与口型正确。
- 上下文 token 数为估算，含附件或部分推理元数据时返回 unknown；自动模型目录与完整旧 Turn 裁剪已接入。摘要失败不再阻止兜底裁剪，受保护内容仍超限时明确拒绝，工具循环中不再改动历史前缀。尚未接入精确 tokenizer 或分块摘要。
- 未调用付费模型、未加载真实 GPT-SoVITS 权重，不能据协议测试宣称供应商能力、设备精度兼容性或实际合成品质已验收。
- 原型 React 文件保持原状。主进程/preload 已切换到新 bootstrap，因此旧页面不能作为新后端使用界面；仅保留仍存在的显示层和草稿测试。旧业务集成测试、旧页面 smoke 与外部模型验收脚本已移除；新的宿主 smoke 仅验证 bootstrap、HTTP 和退出清理，页面测试待 UI 阶段补充。

验证使用 `pnpm test`、`pnpm lint`、`pnpm build`、`pnpm test:python`。新增 `tests/core` 直接验证正式接口，包含真实本机 HTTP/WS 和 AI SDK 对模拟模型服务的请求，不依赖原型页面。

2026-10-15 多目录接入验证：35 项 JS 测试、11 项 Python 测试通过，包含真实 Python 进程握手；lint、构建和 Python 3.9 语法检查通过。宿主冒烟同时验证图片、音频与模型导入的位置。macOS ARM64 临时 `.app` 也通过同一冒烟测试，运行前后 715 个包内文件、目录及链接记录无变化。测试包使用本地 ad-hoc 签名并仅在打包命令中关闭 Hardened Runtime，发布配置未改动；这不代表正式签名、公证、Windows 实机或真实模型推理已验收。打包所需的既有 `zod` 版本已显式列入运行依赖。
