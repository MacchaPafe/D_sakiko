# LLM 连接与推理回传

## 连接定义

`settings.listProviderPresets()` 返回默认地址和协议，不包含 API Key，也不自动创建连接。使用 `saveConnection` 保存独立的 `id/name/baseUrl/models`，同一种兼容协议可以建立多个连接。凭据仍按连接 ID 单独保存，只通过 `credentialConfigured` 向客户端表示是否已配置。

`provider` 是预设和少量厂商参数差异的入口；`protocol` 决定使用哪种消息协议，二者分开。预设包括 Python 首屏的 DeepSeek、火山引擎、Kimi、OpenAI、Anthropic、Gemini、OpenRouter、vLLM、Ollama、Grok、Azure、Hugging Face、魔搭，以及三种通用兼容入口。Azure 仅支持用户给出的兼容端点，不包含传统 Azure 部署、Entra 或独立 API 版本的原生接入；Hugging Face 使用兼容路由。

| protocol               | 实现                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `chat-completions`     | 共享 `@ai-sdk/openai-compatible`；DeepSeek 复用现有原生包；OpenAI 复用 Responses 已需安装的 `@ai-sdk/openai` 的 Chat 方法 |
| `responses`            | `@ai-sdk/openai`，所有生成、摘要和连通检查设置 `store: false`；不使用 `previous_response_id` 或服务端 conversation        |
| `anthropic-messages`   | `@ai-sdk/anthropic`，官方与兼容服务共享序列化                                                                             |
| `google-generative-ai` | `@ai-sdk/google` 的 `generateContent` 路径，不使用 Interactions 会话                                                      |

旧的 `chat`、`openai-compatible` 协议别名在读取或保存时归一化为 `chat-completions`。厂商有多个协议时，预设只提供默认接入；用户可自行选择其他协议并填写端点。所有工具均由本地 Agent 执行，不配置服务端工具。

```js
await settings.saveConnection(
  {
    id: 'my-proxy',
    revision: 0,
    name: '我的代理',
    provider: 'anthropic-compatible',
    protocol: 'anthropic-messages',
    baseUrl: 'https://your-proxy.example/v1',
    behavior: 'generic',
    attachmentTransport: 'inline',
    models: [
      {
        id: 'your-model',
        catalogMapping: null,
        declared: {
          reasoning: { status: 'supported', source: 'user' },
          reasoningEfforts: ['low', 'high']
        },
        overrides: {}
      }
    ]
  },
  { kind: 'replace', secret: apiKey }
)
```

`behavior` 有三个取值：

- `auto`：默认值，仅 Anthropic Messages 协议且端点主机为 `api.anthropic.com` 时采用 Claude 官方规则。
- `generic`：不采用 Claude 的前缀绑定过滤规则。
- `anthropic`：用户明确声明自定义端点为 Claude 代理，采用官方规则。

不会根据模型名或响应含有 `signature` 推断代理的真实服务商。官方规则内部只对已知绑定模型启用过滤，目前匹配 Fable 5.1、Opus/Sonnet/Haiku 5.5 的标准模型名；可通过模型能力 `requiresStablePrefixForThinking` 显式覆盖。此能力仅在官方行为策略启用时生效。

## 思考设置与能力边界

`generation.reasoning` 保留 `enabled/effort`，增加可选 `budgetTokens`。开启思考要求模型能力声明为 `supported`，显式 effort 还必须属于 `reasoningEfforts`。不依据模型名自动推断附件能力。

- Anthropic 未指定预算时使用 adaptive；较旧或只支持手动思考的兼容模型可设置 `budgetTokens`，改用 enabled 模式。
- Gemini 将 effort 转为 thinkingLevel，预算转为 thinkingBudget；具体支持范围由所选模型决定。
- 预算至少为 1024，与 effort 互斥；Responses 和 Completions 不接受统一预算参数。
- DeepSeek 转换 thinking 和 reasoningEffort；Kimi、火山引擎在共享 Completions 请求中添加 thinking 开关。通用 Completions 的显式 effort 使用 reasoning_effort，其余行为遵循端点默认值，不保证所有厂商的私有扩展均已覆盖。
- SDK 报告推理、签名、工具或附件能力被忽略时，Agent 报错，不将结果当作成功。第三方返回不符合所选协议的响应也会报错。

## 存档和回传

业务历史继续使用 `Chat → Turn → Message → parts`。`AssistantMessage.protocol` 保存版本为 1 的可序列化记录：

- `origin`：连接 ID、模型、预设、协议及端点摘要，不保存密钥、credentialScope、HTTP 头或整个 SDK 响应。
- `content`：有序的 SDK text/reasoning/tool-call 块。包含必要的 `providerOptions`，可表示空文本签名和密文。
- `providerOptions`：消息级协议材料，用于回传 OpenRouter 等兼容服务的 `reasoning_details`。
- `projectionFingerprint`：业务片段摘要，避免原始模型 JSON 覆盖用户修改的台词。
- `contextFingerprint`：本轮输入与此前业务历史摘要，检测编辑或摘要压缩后的回传材料失效。

元数据只保留必要字段：Anthropic 的 signature/redactedData，OpenAI 的 itemId/reasoningEncryptedContent/phase/parallelToolCall，Google 的 thoughtSignature，以及兼容接口 reasoning_details 中的类型、内容、签名、格式和顺序字段。可见推理文本仍保存为 ReasoningPart。签名和密文不进入摘要输入，其字节长度也不用于推算输入 Token 数；存在此类材料时预算估算返回 unknown。

同一工具循环完整回传原响应，包含工具前文字和签名；并行工具结果在所有调用之后统一回传。格式修复保留已经产生的响应和工具结果，不重新执行整轮。连接、凭据和生成参数在一轮开始时冻结。

下一轮不保存或重放旧 runtime_controls：

- 普通服务仍保留有效的原始推理材料，不因控制消息移除而统一清空。
- 启用 Claude 前缀绑定规则时，从请求副本中移除历史 thinking/redacted_thinking；本地原始记录不变。
- 连接、端点或模型改变，或者业务历史被编辑、压缩后，不再复用受影响的原始协议块，改由普通对话和工具记录构建上下文。此处理也会放弃受影响的历史推理连续性。
- 新旧 SDK、第三方端点可能有额外约束；本实现不做猜测性重试，也不伪造签名。兼容服务应返回所选协议规定的完整字段。

前缀限制依据 [Anthropic 排错说明](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting)；未将它扩展到 [OpenAI](https://developers.openai.com/api/docs/guides/reasoning) 或 [Gemini](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)。

## 验证

`tests/core/llm-protocols.test.js` 使用实际 AI SDK 与内存 HTTP 响应，验证各协议的工具循环、空推理元数据、JSON 存档恢复、官方与兼容策略差异、连接切换和历史编辑。它不读取 `.env` 或调用外部 API。

手工真实测试独立于 Vitest 和 npm scripts：

```sh
node scripts/manual/check-deepseek.mjs --run-live
```

脚本只读取仓库根目录 `.env` 的 `DEEPSEEK_API_KEY`，仅访问 DeepSeek 官方端点，最多三次请求；临时连接、密钥文件及测试存档在结束后清理。不输出密钥、HTTP 头或原始 SDK 异常。

2026-10-25 已运行一次：`deepseek-flash`，两轮、三次请求、一次本地工具调用，验证真实推理回传、磁盘 JSON 恢复、第二轮复述工具结果以及控制消息不入历史。OpenAI、Anthropic、Gemini 和其他厂商仅完成离线协议验证，未进行收费 API 实测。
