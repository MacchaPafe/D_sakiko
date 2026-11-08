# Python 统一生成入口与存档迁移说明

## 入口和职责

公共调用入口为 `GPT_SoVITS/llm/client.py` 的 `generate`。传入 `LLMRequest`、`LLMConfig` 和可选的 `CancellationToken`，返回完整 `LLMResult`，失败抛出 `LLMError`。模块不读取全局 Qt 设置，不执行工具循环，也不主动裁剪调用方提供的历史。

| 模块 | 职责 |
| --- | --- |
| `llm/types.py` | 项目请求、结果、连接和取消类型 |
| `llm/configuration.py` | 从调用方配置快照解析连接 |
| `llm/client.py` | 完整请求执行、取消和既有魔搭限流策略 |
| `llm/adapters/chat_completions.py` | LiteLLM 供应商路由及 DeepSeek 图片 SDK 路径 |
| `llm/adapters/responses.py` | Responses 输入转换、内部 SSE 与完整输出解析 |
| `llm/response_routes.py` | 普通 Responses 与 ChatGPT 订阅的请求契约、模型能力校验 |
| `llm/chatgpt/` | 本机 OAuth、账户与凭据存储、刷新、目录及订阅认证恢复 |
| `llm/capabilities.py` | 保守处理本地已知参数能力 |
| `llm/replay.py` | 协议材料白名单、来源及历史指纹 |
| `chat/archive.py` | 共同历史、旧存档转换和现有 Message 投影 |
| `chat/portable.py` | 与 Electron 一致的 v2 对话交换包 |

日常对话的普通生成、工具调用、格式修复和摘要，以及小剧场生成均进入该模块。工具循环仍在 `chat/tool_calling.py`，本地摘要和窗口选择仍在 `chat/rolling_summary.py`。世界书离线标注暂时保留原入口。

## 配置和参数

在配置程序中选择“自定义提供商（OpenAI Responses 协议）”，填写 Base URL（通常到 `/v1`）、模型 ID 和 API Key。该项与旧自定义提供商的值独立保存，可往返切换。配置页沿用 qfluentwidgets 的 ComboBox、LineEdit 和 PasswordLineEdit；聊天和小剧场窗口沿用标准 Qt。

两种协议均不增加通用自动重试。保留魔搭临时限流的有限重试，额度耗尽立即报告。输出截断、连接中断或 Responses 未收到完成事件均不返回完整成功结果；业务格式修复由调用方决定。

程序不能从模型名称可靠地推断所有能力。仅对可信官方地址且被本地 LiteLLM 模型目录识别的模型，省略能力表明确不支持的 temperature/top_p，并在结果 diagnostics 中说明。第三方自定义地址、模型别名和未知能力保留参数；服务端拒绝时返回明确错误。工具、图片和结构化输出等功能要求不自动删除。

Responses 使用 `store=false`，明确请求加密推理材料，以 base64 data URL 提供图片。没有 `previous_response_id`、服务端 conversation 或 Files 图片引用。内部流式传输仅用于完成事件识别和取消，对调用者没有增量接口。reasoning 开关映射为 Responses 的 effort：关闭使用 none，仅开启但未指定强度使用 medium；具体模型不接受时报告错误。

## 历史和投影

共同结构是 `Chat.turns[].messages[]`。一次完整模型响应对应一个 AssistantMessage，其中推理、工具调用和已验收台词分别存储。工具结果是独立消息；多句台词属于同一个响应的 dialogue part。台词、消息和轮次拥有稳定 ID，语音回执优先按台词 ID 关联。

界面和 TTS 使用用户输入/台词投影，推理不显示。世界书工具存储完整结果但没有可见卡片。摘要读取权威历史中的工具结果，隐藏状态不会丢掉摘要所需知识；摘要输入沿用有界、脱敏的工具轨迹。原始存档不按该摘要长度截断工具结果。

模型投影保持原生响应和工具的顺序。可复用材料必须同时满足连接凭据作用域、提供商、协议、模型及此前请求内容相同；本机历史编辑或外部修改会使其失效。密文和签名只作为协议材料，不转换成可见台词。系统提示、世界书上下文、压缩窗口或连接变化也可能改变前缀，使旧推理不再复用。

`Message` 和 `message_list` 保留给现有 UI 的访问方式，直接修改共同节点。编辑台词仍可逐句进行，并沿用保留旧音频的行为；已有音频的 spoken 文本保留原值，明确重新生成音频后才更新。删除和分叉同步处理隐藏过程。小剧场编辑保存失败时恢复完整节点，不通过平面台词列表重建隐藏历史。

## 自动迁移和恢复

启动首次加载旧存档时自动执行迁移，无需手动运行脚本。过程为：取得文件锁 → 复制完整原文件 → 转换和校验所有会话 → 写报告 → 原子替换。没有直接修改用户现有文件进行本次开发测试。

备份位于原文件同级的 `backup/`，文件名包含 `.before-timeline-<UUID>.bak`；报告为 `<原文件名>.migration-report.json`。报告列出每条对话的警告及迁移状态。失败保留原文件；原始备份仍可恢复。新存档外层增加 `conversation_schema_version: 1`。

旧存档的可恢复范围：

| 内容 | 处理 |
| --- | --- |
| 台词、翻译、情绪、演出、场景、静音、附件和音频路径 | 转为共同字段或 Python 扩展，稳定身份尽量沿用 |
| 已保存的 WebUI 消息和轮次身份 | 可确定时沿用 |
| 工具调用记录 | 重建调用与结果，标明可能截断、分组无法确定 |
| 滚动摘要 | 扩大至所在完整 Turn，报告原/新边界，保留原文 |
| 原始响应分组、推理、历史请求参数 | 未曾存储的部分不伪造，报告缺失 |
| 历史语言、形态 | 缺失时为未知；不套用当前设置补历史 |
| 无法识别的旧字段 | 保留到扩展；无效结构报错 |

保存失败后内存中的已完成结果继续保留，新的生成和历史修改入口停止，重试保存成功后恢复操作。主窗口和小剧场已有保存入口；WebUI 可使用 `save_chats`。关闭时仍提供重试保存或明确放弃的流程。取消或重启不会自动重新执行工具；结果未知的工具会标记为中断。

## 与 Electron 交换

默认导出 `.dsakiko.json`，内容为 `format: dsakiko`、`version: 2`、`chats`、`resources`。Chat 和资源引用使用 `feat/v5-electron` 实际合同；音频、图片等资源以内联字节传递，缺失资源给出警告并保留原引用。旧 Zip 备份仍可导入，指定 `.zip` 也可继续导出旧包。

已用 Electron 分支实际存储模块完成 Python 导出、Electron 导入和再次导出、Python 导入的往返验证，音频字节一致。ChatGPT 登录的后续实现见下文；尚未进行真实账户授权或收费 API 的模型调用，也未宣称所有第三方 Responses 服务的参数兼容性。

## ChatGPT 订阅接入

### 协议、路由与模型能力

普通 API Key 与 ChatGPT 订阅共用 Responses 编解码、内联图片、工具结果投影、推理材料和 SSE 完成判定。连接快照增加明确的 `route`，不从令牌外形或域名猜测计费方式。普通 Responses 的 `standard` 契约不应用订阅参数限制；订阅使用 `chatgpt-subscription`。

`response_routes.py` 集中持有订阅规则。流程是共同编码 → 合并 extra_body → 执行一次路由规则 → 检查账户模型目录中明确给出的能力 → 发送。规则只返回请求副本和 diagnostics，不修改保存的配置。UI 从同一个规则读取参数提示，避免另抄一份限制名单。

| 限制性质 | 行为 |
| --- | --- |
| temperature、top_p、max_output_tokens 等偏好 | 订阅请求中省略，保留原配置并记录 diagnostics；Fluent 页面禁用采样参数 |
| 服务端历史、后台执行及不支持的功能字段 | 发送前报错，包括嵌套 extra_body，不偷偷改变功能要求 |
| 本地执行的函数工具 | 编码为 dsakiko namespace；工具结果仍由原有业务循环处理 |
| 服务端托管工具 | 当前实现拒绝，不推断客户端执行就能兼容托管工具 |
| 模型、推理强度、图片能力 | 模型须属于当前账户；目录明确不支持才本地拒绝，未知能力交由服务端判断 |

订阅始终发送本地完整上下文、`store=false`、`stream=true`；system 提示转为 developer。对业务层仍只返回完整结果，不增加 Agent 循环要求或流式接口。

### 登录和账户

Fluent 配置的“ChatGPT 订阅（登录 ChatGPT）”页面提供统一的 Continue with ChatGPT、退出登录、复制登录链接、取消、刷新模型和管理用量。账户下拉框选择已保存账户时，统一登录按钮复用原注册；选择“添加其他账户”时走新注册。不再提供独立的重新登录或移除账户按钮。首次成功授权展示欢迎说明。登录通过系统浏览器、PKCE S256 和临时 `127.0.0.1` HTTP 回调完成；交换令牌及推理使用 HTTPS。不接受手工粘贴回调网址。

新注册先保存回调签发的 Client ID，再交换授权码。验证 ID token 的签名、issuer、audience、有效期、subject 和 nonce；相同邮箱不自动合并不同注册。有效身份缺少订阅授权时保留登录，禁止推理，允许用户重新授权。

Client ID 标识绑定特定用户和工作区的 OAuth 注册及其用量设置，不是账户 ID 或访问凭据。退出登录始终保留账户映射、Client ID 和本机 Host ID，仅撤销会话并清理令牌。再次授权前就须选择复用哪个注册，不能在新注册完成后只按邮箱合并；原注册返回其他已验证身份时拒绝替换。待登录账户在 OAuth 成功验证前不会覆盖当前活动账户。普通退出后的登录不强制再次同意授权；只有已验证身份缺少订阅权限、用户明确请求授权时才使用 consent。

账户和模型分开保存：只有一个全局 ChatGPT 模型。首次用户手选模型后启用；添加账户成功后立即选中新账户并沿用已有模型。选择账户或模型立即保存和通知主程序，只提交相关字段，不覆盖其他配置草稿。目录不支持所选模型时要求用户调整，不自动换模、换账户或改为 API Key 计费。日常对话保持本轮配置快照，修改从下一轮生效。

主程序使用标准 Qt 的一次性可关闭横幅，点击打开 Fluent 配置中的订阅页面。WebUI 不增加登录流程。

### 凭据与恢复

凭据位于 platformdirs 的 D_sakiko 用户数据目录下的 chatgpt 子目录，优先使用系统 keyring；写入失败静默降级为独立私有文件。注册表仅保存非秘密元数据及权威凭据位置，降级后不再读取旧钥匙串副本。Unix 使用私有目录及 0600 原子文件；Windows 为目录设置当前用户 ACL。令牌不写入普通配置、共同对话时间线或协议回传材料。

跨进程会话锁串行化刷新和退出；轮换后的完整令牌集一起写入。生成持有账户使用租约，退出在账户忙碌时拒绝。临近过期主动刷新；仅在流打开前收到明确的失效令牌错误时额外刷新并重试一次。确认刷新令牌不可用才清理秘密并保留注册，临时网络或 503 错误保留凭据。注销尝试远端撤销，失败仍停用本地登录并说明结果。

短期 access token 轮换不改变协议回传材料的作用域；作用域绑定稳定注册身份、账户、模型及历史前缀。账户切换或注册变化会使旧材料失效。

传输错误保留实际 HTTP 状态、服务端错误码、参数、body 和 request ID，用户用量错误提供 manage_usage 动作，临时服务故障提供 retry_later 动作。SSE 中的 response.failed 保留流的实际 HTTP 200，不捏造 HTTP 429；缺少 response.completed 不能提交成功。生成不自动重试 503 或额度错误。

仅订阅路径添加自身 User-Agent、稳定 session-id/thread-id 和每次尝试独立的 x-client-request-id，集中在订阅 runtime 中。它们用于关联和兼容，不假定能修复全部网关故障，不冒用其他客户端身份。

### 验证边界

离线测试使用内存凭据库、临时目录、模拟 HTTP 和真实 loopback 回调，覆盖签名与 nonce、PKCE、Client ID 持久化、身份授权分离、并发刷新、文件降级、账户使用租约、错误形状及真实 SDK 的 SSE 解析。Fluent 自动保存与全局模型选择、标准 Qt 横幅均有离屏交互验证。真实 OpenAI 账户授权、实际订阅推理及 Windows 凭据库/ACL 仍需对应环境验证。

规则来源核对于 2026-11-08：[官方限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)、[模型和推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[错误恢复](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)。
