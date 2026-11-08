# Python 统一模型请求与共同对话时间线

已接受，2026-11-08。

## 决定

Python 的日常对话和小剧场通过 `GPT_SoVITS/llm/client.py` 的 `generate(request, config, cancel)` 请求模型。`LLMConfig` 是调用方传入的连接快照，`LLMRequest`、`LLMResult` 和 `LLMError` 属于项目自身。请求模块只负责模型通信、协议适配、取消和错误分类，工具执行、格式修复、摘要及上下文选取仍归业务运行时负责。世界书离线标注流水线不在此次入口迁移范围内。

保留 LiteLLM 的现有 Chat Completions 供应商路由，并增加 API Key 认证的 Responses 后端。外部只能获得完整结果；Responses 的 SSE 在模块内消费。请求使用本地准备的全部上下文、`store=false` 和内联 base64 图片，不使用服务端历史 ID 或 Files API。此次不接入 ChatGPT 登录或 OAuth。

配置程序的“自定义提供商（OpenAI Responses 协议）”使用独立 URL、模型和 Key，与旧自定义 Chat Completions 配置共存。交互控件使用 qfluentwidgets；主程序继续使用标准 Qt。

## 对话模型

采用 Electron `feat/v5-electron` 已有的共同 Chat、Turn、Message 结构。AssistantMessage 的有序 parts 包含 reasoning、tool-call 和 dialogue，台词拥有独立稳定身份；ToolResultMessage 和 AbstractMessage 属于同一消息序列。Python 的 `message_list` 是兼容现有 UI 和语音模块的可写投影，修改直接落到共同节点，不再保存另一份平面消息正文。

推理内容不在聊天界面展示，世界书工具的调用及结果正常存储但隐藏；普通工具保留现有展示方式。本决定在 Python 对话存储范围更新 [ADR-0034](0034-deliver-worldbook-knowledge-through-direct-context-and-hidden-tools.md) 中“静默工具不产生聊天历史消息”的约定：该规定改为不产生可见工具卡片、过渡回复或语音，允许在共同历史中保留工具过程。独立世界书诊断的保留规则不受影响。

协议回传材料只保存白名单字段，包括加密推理及签名。连接作用域、协议、模型和输入前缀都匹配时才复用。编辑、截断、摘要替换或切换连接后可能无法继续复用旧推理；原材料保留，不把失效材料冒充当前请求的材料。正文、台词和工具结果仍可跨协议投影。

## 持久化与迁移

Python 继续使用现有会话文件位置。首次加载旧 `message_list` 存档时，在同一文件锁内备份原文件、转换、校验、写迁移报告并原子替换。失败保留原文件并报告错误。未来消息类型或更高外层版本不能被旧程序覆盖。

旧记录无法恢复原始模型响应分组、未保存的推理、已丢失或截断的工具结果及历史生成设置；迁移报告明确记录这些缺失。摘要覆盖边界扩大到所在 Turn 末尾，保留原始消息并报告扩大范围。旧消息未知的语言和形态保持未知。

模型失败或取消时保留已接纳用户输入、完整响应及工具结果；未完成响应不作为完整消息入档。没有确认结果的工具标记为 interrupted，重启不重跑。保存失败保留内存内容并阻止继续生成；用户可重试保存。

共同交换包使用 Electron 的 `format=dsakiko, version=2`，资源以内联字节及 AssetRef 交换；Python 本机路径留在本地扩展内。继续接受旧 Zip 备份。文件位置、Python 专有演出和世界书快照等以扩展保存，不增加协议专属存档格式。

## 验证

离线验证覆盖两种传输适配、取消、完整性、推理回传与失效、资源交换、迁移失败保护、摘要边界、并发保存及旧 UI 投影。另使用 Electron 分支实际 ChatStore 导入器与导出器，验证 Python → Electron → Python 的对话及 WAV 资源往返。真实服务商、OAuth 和用户实际存档迁移需要独立运行验证。
