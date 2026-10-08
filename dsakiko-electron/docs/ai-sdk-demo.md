# AI SDK 使用与接口验证

AI SDK 可以统一厂商请求、原生工具调用和结构化输出。项目仍需控制通知顺序、工具副作用、取消及业务校验。独立 demo 位于 [`scripts/demos/ai-sdk`](../scripts/demos/ai-sdk/)，依赖在自己的 `package.json` 和 `package-lock.json` 中锁定，不接入现有应用启动流程。

## 运行

使用 Node 22.12 以上版本，最初接口验证使用 Node 24.21.0、`ai@7.0.130`；本次能力检查补充使用 Node 26.10.0，依赖版本保持锁定。

```sh
cd dsakiko-electron/scripts/demos/ai-sdk
npm ci --ignore-scripts
npm test
```

离线测试不读取本机凭据，也不调用外部服务。真实请求需复制 `.env.example` 为 `.env.local`，填写 API Key 和模型后运行：

```sh
node --env-file=.env.local demo.mjs --mode=all
```

可分别选择 `completion`、`automatic`、`controlled`。也可显式读取旧配置当前选择的内置供应商：

```sh
node demo.mjs --mode=all --legacy-config=/绝对路径/d_sakiko_config.json
```

真实模式会消耗厂商额度，只发送演示提示词和只读时间工具结果。运行输出包含台词与事件顺序，不输出凭据或完整 SDK 错误对象；`.env.local` 已被项目忽略。旧自定义服务需通过环境变量明确指定协议，不根据地址猜测。

## 常见操作

| 操作           | 入口                                                       | 用法                                                                                                                |
| -------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| 选择供应商     | [`providers.mjs`](../scripts/demos/ai-sdk/providers.mjs)   | `createModel(connection)` 返回绑定凭据和地址的模型；demo 支持 DeepSeek、Anthropic、Google、自定义 OpenAI Compatible |
| 普通补全       | [`operations.mjs`](../scripts/demos/ai-sdk/operations.mjs) | `generateText({ model, prompt, abortSignal })`，从 `result.text` 取得完整文本                                       |
| 自动工具循环   | 同上                                                       | `ToolLoopAgent` 注册 `tool({ inputSchema, execute })`，自动执行工具和回传结果                                       |
| 结构化输出     | 同上                                                       | `output: Output.object({ schema: replySchema })`，从 `result.output` 取得已校验对象，可与工具循环组合               |
| 接口顺序与重试 | [`agent.mjs`](../scripts/demos/ai-sdk/agent.mjs)           | 工具不注册 `execute`，收到 SDK 解析的调用后，按项目约定通知并执行                                                   |

厂商直连示例：

```js
const model = createModel({ provider: 'deepseek', model: modelName, apiKey })
const result = await generateText({
  model,
  prompt: '请回复一句问候。',
  maxRetries: 0,
  abortSignal: signal,
  telemetry: { isEnabled: false }
})
```

厂商提供默认地址，用户仍需选择模型；自定义兼容服务还需 `baseURL`。模型列表、能力声明、连接管理及设置界面由应用维护；可查询的外部能力元数据及其适用范围见下节。`providerOptions` 用于经过验证的厂商专属参数，SDK 不保证所有模型支持相同能力。

工具参数沿用现有 `AgentTool.parametersSchema`。**`jsonSchema(schema)` 默认不执行参数校验**，demo 通过 Ajv 编译 Schema，再提供给 `jsonSchema(schema, { validate })`；也可以直接使用带验证能力的 Zod Schema。`execute` 和密钥都留在 Node，不进入共享协议。

## 模型视觉、文件输入与上传能力检查

补充探索日期：2026-10-07。此前本文只有“能力声明由应用维护”的原则，没有专项验证；需求表 `ASSET-02` 仍把“AI SDK 是否能确定模型能力”列为待探索。

**结论：当前锁定的 AI SDK 本体没有统一模型能力查询 API，但生态里已有 Models.dev 公共目录及官方客户端 `@opencode-ai/models`，可以为已收录模型查询视觉、PDF 等输入能力，不必由每个应用从零维护完整能力表。目录提供能力声明，不自动探测任意自定义端点；Files API 上传能力仍需单独确认。**

本次核对实际安装源码：`ai@7.0.130`、`@ai-sdk/provider@4.0.24`、`@ai-sdk/gateway@4.0.106`，以及 demo 锁定的四种适配器。以下结论以这些版本为准。

| 所需判断 | 当前支持情况 | 边界 |
| --- | --- | --- |
| 任意模型是否支持视觉或某种附件 | **没有统一能力查询接口** | `LanguageModelV4` 只声明模型身份、`supportedUrls` 和生成方法，没有标准视觉开关或支持的 MIME 类型全集；适配器内的模型分支也不构成统一查询服务。 |
| 通过补充库查询已收录模型的能力 | **支持：Models.dev 官方客户端 `@opencode-ai/models`** | 以供应商 ID 和模型 ID 查询 `modalities.input`、`attachment`、工具调用及 token 限制等；不要求模型请求经过 Gateway，未收录模型返回缺失。 |
| 某 URL 能否直接交给厂商读取 | **可读取 `model.supportedUrls`** | 它控制 URL 透传与 SDK 下载，不能判断模型是否理解附件。为空也可能支持内联图片；有值也不保证模型存在。 |
| 某供应商适配器是否实现文件上传 | **可检查 `typeof provider.files === 'function'`** | 这是适配器级检查，不确认当前端点、账号、模型或 MIME 类型实际可用。 |
| 使用统一 API 上传文件 | **支持 `uploadFile({ api: provider.files(), data, mediaType, filename })`** | 返回 `providerReference`，可在消息中引用；上传成功不保证某个模型能读取该引用。支持哪些文件由供应商实现和服务约束决定。 |
| 通过 `gateway.getAvailableModels()` 取得能力标签 | **本次锁定版本不支持** | 请求 Gateway 的 `/config`，返回名称、描述、价格、模型类型和身份；Schema 不保留 `tags`、`inputModalities` 等字段。 |
| 查询 Gateway 目录中的视觉和文件输入声明 | **可直接请求公共 REST `GET https://ai-gateway.vercel.sh/v1/models`** | 无需凭据；条目的 `tags` 可含 `vision`、`file-input`。这是 Gateway 目录声明，不验证项目自定义 `baseURL` 上的同名模型；`file-input` 也不表示任意文件类型或独立上传接口可用。 |

### 为什么不能用 `supportedUrls` 判断视觉

[官方模型接口说明](https://ai-sdk.dev/providers/community-providers/custom-providers)及 `LanguageModelV4` 源码明确将它定义为按媒体类型匹配的 URL 透传规则。实验中：

- 构造一个不存在的 Anthropic 或 DeepSeek 模型，不发出网络请求，仍取得与正常模型名相同的 `supportedUrls`。因此有 `image/*` 规则不能证明该模型支持视觉。
- 自定义 OpenAI Compatible 模型的 `supportedUrls` 默认是 `{}`，仍可把 PNG 字节组装为 `image_url` 的内联 data URL 并发送。因此没有规则也不能证明不支持视觉。

第二项只验证 SDK 组装请求的能力，响应来自夹具，**没有证明真实服务接受图片或模型识别正确**。对不支持的输入，不同适配器可能抛错或返回 warning；不能只看请求是否成功而忽略附件是否被保留。

### Gateway 目录能提供什么

[Gateway SDK 文档](https://ai-sdk.dev/providers/ai-sdk-providers/ai-gateway#dynamic-model-discovery)提供模型发现入口，但本次源码与夹具实测确认：即使 `/config` 响应含能力字段，`getAvailableModels()` 的 Schema 也会将其丢弃。不要直接读取 `models[i].tags` 来判断视觉。

[Gateway REST 文档](https://vercel.com/docs/ai-gateway/sdks-and-apis/rest-api#list-models)另有公开的 `/v1/models`，明确返回能力标签。本次真实只读请求返回 414 个目录条目：`anthropic/claude-sonnet-4.5` 和 `google/gemini-2.5-flash` 的标签均含 `vision`、`file-input`；`deepseek/deepseek-v4-pro` 的标签不含这两项。后者只能记录为“本次 Gateway 目录未声明”，不能据此否定 DeepSeek 直连的能力。接口还提供 `/v1/models/{creator}/{model}/endpoints`，其中 `architecture.input_modalities` 描述输入模态，同样没有给出通用上传协议或完整 MIME 限制。

应用如使用此目录，应保留 Gateway 模型 ID、来源及查询时间。名称匹配或目录标签缺失都不足以为自定义端点生成确定的支持/不支持结论。

### 文件上传已经支持，但与模型输入能力分开

[官方 `uploadFile` 文档](https://ai-sdk.dev/docs/reference/ai-sdk-core/upload-file)说明统一上传及 `ProviderReference` 用法。本次实际安装的 DeepSeek、Anthropic、Google 适配器都提供 `files().uploadFile`；`@ai-sdk/openai-compatible@3.0.65` 没有 `files()`，即使某个兼容服务自己提供 Files API，也需要另行适配。

尤其不能把“支持上传”合并成一个不区分文件类型的布尔值：`@ai-sdk/deepseek@3.0.61` 的上传实现仅接受 JPEG、PNG、GIF、WebP 图片，并限制大小不超过 64 MiB。离线实验通过统一 `uploadFile` 发送图片，确认 `/files` multipart 请求和 `{ deepseek: 'file-fixture' }` 引用返回；同一 API 发送 PDF 在网络请求前就被拒绝。这里验证的是适配器协议和本地校验，未调用真实上传服务。

`FilesV4` 要求实现上传，元数据读取、下载、删除为可选方法；已核对的这三种直连适配器目前只有上传实现。现有 `RemoteFiles` 所需的作用域隔离、并发复用、处理完成等待、引用失效和删除回收，仍需项目实现，不能因为引入 `uploadFile` 就视为这些职责已经满足。

### 现成补充库：优先 Models.dev 官方客户端

同日继续探索确认：上一轮“AI SDK 本体无通用查询接口”的判断成立，但“应用维护能力”不应理解为应用必须手填整张模型表。已有共享目录和客户端可复用：

| 方案 | 能提供什么 | 与当前项目的关系 |
| --- | --- | --- |
| [Models.dev 官方客户端 `@opencode-ai/models`](https://github.com/anomalyco/models.dev/tree/dev/packages/sdk) | `Models.make().providers()` 查询供应商模型；`models()` 查询模型本身；`catalog()` 查询两者；另有包内离线 snapshot。 | **优先选择。** 查询元数据与 AI SDK 的生成请求相互独立，可保留当前直连适配器，不必接入代理服务。 |
| [TokenLens / `@tokenlens/fetch`](https://github.com/xn1cklas/tokenlens) | 封装 Models.dev 目录查询；同一生态还提供静态目录、上下文预算与费用辅助函数。 | 也是可选轮子，但底层能力数据仍来自 Models.dev，不构成另一份独立验证。本轮只核对文档，未安装或实测。 |
| [OpenRouter 模型 API](https://openrouter.ai/docs/api/api-reference/models/get-models) + [`@openrouter/ai-sdk-provider`](https://github.com/OpenRouterTeam/ai-sdk-provider) | 模型目录的 `architecture.input_modalities`、`supported_parameters` 等字段；provider 负责 AI SDK 请求。 | 适合实际通过 OpenRouter 调用的连接；目录语义属于 OpenRouter，不能直接当作任意直连端点的能力保证。本轮未安装或实测其 provider。 |

[Models.dev 项目说明](https://github.com/anomalyco/models.dev)将模型 ID 用于 AI SDK 对接，供应商条目还带 `npm` 适配包信息。其 Schema 的 `modalities.input` 区分 `text`、`image`、`audio`、`video`、`pdf`，并提供 `attachment`、`tool_call`、`reasoning`、`structured_output`、`limit` 等字段。查视觉应读取 `image`，查 PDF 应读取 `pdf`；`attachment: true` 不能推导出接受任意文件，更不能推导出提供远端上传 API。

官方 SDK 的最小用法如下；它是元数据客户端，没有 `inspect(aiSdkModel)` 这样的自动探测调用，应用按自己选择的供应商和模型 ID 做关联即可：

```js
import { Models } from '@opencode-ai/models'
import { createGoogleGenerativeAI } from '@ai-sdk/google'

const providers = await Models.make().providers({ signal: AbortSignal.timeout(5000) })
const modelId = 'gemini-2.5-flash'
const metadata = providers.google?.models[modelId]
const supportsVision = metadata?.modalities.input.includes('image')
const supportsPdf = metadata?.modalities.input.includes('pdf')

// 查询目录不需要厂商密钥；真实生成仍使用原来的 AI SDK 适配器和凭据。
const model = createGoogleGenerativeAI({ apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY })(modelId)
```

`metadata` 缺失时，上面的两个结果为 `undefined`，应按“未知”处理。模型本身的公共元数据与特定供应商的服务配置可能不同，应优先使用匹配供应商的条目；自定义端点通过显式映射引用已知模型，并允许覆盖，不能单凭同名就视为已验证。

本轮在临时目录安装 `@opencode-ai/models@0.0.96`，使用 Node 26.10.0 与当前 demo 的 `ai@7.0.130` 做了真实公共目录查询及模型对象构造，未增加项目依赖，也未发起模型生成：

- `providers()` 返回 226 个供应商条目。Google `gemini-2.5-flash` 声明 `text/image/audio/video/pdf` 输入；Anthropic `claude-sonnet-4-5` 声明 `text/image/pdf`。两者 `attachment: true`，返回的原生模型 ID 均能交给现有 demo 适配器创建模型对象。
- DeepSeek `deepseek-v4-pro` 本次目录条目为 `attachment: false`、仅 `text` 输入，且 `npm` 推荐 `@ai-sdk/openai-compatible`；项目当前使用 `@ai-sdk/deepseek`。这说明目录条目与 SDK 适配器不能简单等同，更不能从“适配器有图片上传代码”推定具体模型支持视觉。
- 不存在的模型 ID 查询得到 `undefined`；包内 snapshot 能离线查到同一 Google 模型，`generatedAt` 为 `2026-10-06T05:33:48.234Z`。

这些结果证明客户端可以提供可消费的真实目录元数据并与当前 AI SDK 并用，**没有验证模型真实识图、PDF 处理或远端上传**。目录数据来自项目维护的 TOML 及其同步流程，会存在延迟或错误；查询时间应由本地记录，不能把模型的 `last_updated` 字段当作本次端点验证时间。

另外，已核对的官方客户端普通入口无运行时依赖，不要求安装 Effect，也不依赖特定 AI SDK 主版本；每次调用发出一次 GET，**不内置缓存**。正式接入应加缓存与超时，网络失败时采用明确标记时间的旧缓存或 `@opencode-ai/models/snapshot`；snapshot 随包发版更新，已安装的副本不会自行刷新。

### 项目接入结论与复现

对于 `ASSET-02`，建议通过 Models.dev 官方客户端取得已收录供应商及模型的默认输入能力，由连接配置保留显式模型映射、局部覆盖和未知状态，避免从零维护完整模型表。另行维护具体图片/文档 MIME 类型及 inline、URL、远端引用等传输约束，状态区分“支持、不支持、未知”。能力证据应绑定供应商、实际端点、模型与必要的账号作用域，并记录来源和查询或验证时间。厂商文档或适用目录可以提供声明，真实小样本请求可以验证特定组合；未知状态不能因采用 base64、存在 `files()` 或模型同名就自动升级为支持。这是接入建议，尚未实现为正式应用功能。

新增 [`tests/capabilities.test.mjs`](../scripts/demos/ai-sdk/tests/capabilities.test.mjs) 的 5 项离线实验，使用真实 SDK、合成凭据和可控 `fetch`，可用以下命令独立复现：

```sh
cd dsakiko-electron/scripts/demos/ai-sdk
node --test tests/capabilities.test.mjs
```

本次在 Node 26.10.0 上新增 5 项全部通过；连同原有 33 项，共 38 项通过。未读取本机凭据、调用真实模型或上传真实文件；线上验证仅查询无需凭据的 Gateway 与 Models.dev 公共模型目录。

源码核对入口（运行 `npm ci --ignore-scripts` 后位于 demo 的 `node_modules`）：`@ai-sdk/provider/src/language-model/v4/language-model-v4.ts`、`@ai-sdk/provider/src/provider/v4/provider-v4.ts`、`@ai-sdk/provider/src/files/v4/files-v4.ts`、`@ai-sdk/gateway/src/gateway-fetch-metadata.ts`、`@ai-sdk/gateway/src/gateway-model-entry.ts`、`@ai-sdk/deepseek/src/files/deepseek-files.ts`，以及各适配器的 provider 实现。

## 对齐 Agent 接口

正式约定见 [`llm/interface.js`](../src/backend/llm/interface.js)。demo 的 `runAgent` 验证下列执行规则：

| 接口要求                     | 实现方法                                                                                                                |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| reasoning 先接受，再执行工具 | 单次 `generateText` 不注册工具 `execute`；依次 `await onIntermediate`，再 `await onToolActivity(running)`，最后执行工具 |
| 可见与隐藏工具               | `visibility=visible` 才通知工具活动；隐藏工具仍执行并把结果提供给模型                                                   |
| 同一调用更新同一消息         | 保留 SDK 的 `toolCallId` 作为 `callId`；同一 ID 的 `running` 和终态更新同一条记录                                       |
| 通知失败后停止               | Observer 拒绝直接结束本轮；终态通知失败也不重新执行工具                                                                 |
| 格式失败仍保留 reasoning     | 先接受完整 reasoning，再解析台词 JSON 并执行 Zod 与业务校验；最终台词只通过返回值交付                                   |
| 格式修复不重放工具           | 保留已执行结果和 SDK 历史，修复请求移除工具定义；本地再次拒绝修复期间的工具调用                                         |
| 防止同 ID 重复执行           | 本轮记录已完成 `callId` 的结果；同 ID、同参数复用结果，换工具或参数则拒绝                                               |
| 取消及超时                   | 合并用户信号与整轮时限，传给 SDK 和工具；每个异步边界检查信号，不继续接受迟到输出                                       |
| 有限重试                     | `maxRetries: 0` 关闭 SDK 请求重试；项目单独限制总请求步数和格式修复次数                                                 |
| 正确回传工具历史             | 追加 `result.response.messages`，再写入标准 `role: 'tool'` 结果，不把工具结果伪装成用户文本                             |
| 台词语义合法                 | Schema 保持固定，额外核对角色 ID 及该角色的动作、表情目录                                                               |

`ToolLoopAgent` 适合普通自动循环。**当前 SDK 的 `onStepEnd` 和 `onToolExecutionStart` 回调抛错不会阻断生成或工具执行**，已通过实际 SDK 测试确认，不能把业务 Observer 直接接到这些钩子并依赖异常停止。demo 在 SDK 调用之外显式 `await` Observer，采用受控循环安排顺序；仅手动解析最终台词 JSON，厂商工具报文和参数解析仍由 SDK 完成。

`Output.object` 在自动循环中展示 SDK 的结构化能力；受控循环先取得完整响应再做台词校验，使校验失败不会阻止 reasoning 被接受。需要同时使用严格结构化模式和上述通知语义时，应另行验证生命周期钩子的执行时机。

当前运行内的 SDK 消息应原样保留：DeepSeek 的 `reasoning_content`、Anthropic 的 thinking signature、Google 的 `thoughtSignature` 不能从展示文本重建。它们只保存在本轮运行历史；现有 `ContextMessage` 不承诺跨重启精确重放厂商请求。

去重只覆盖同一次运行的相同 `callId`；不同 ID 的相同工具调用仍是新调用，跨运行的业务幂等需要工具自身实现。取消不会回滚已发生的外部副作用；工具和 Observer 必须提供有界等待，工具应响应 `AbortSignal`。Conversation 仍需拒收旧运行回调，并在停止或失败后收尾未完成工具记录。

## 验证结果

验证日期：2026-10-07。

- 离线协议与边界测试：原有 33 项加能力检查 5 项，共 38 项通过。使用真实 SDK 和厂商适配器，仅以可控 `fetch` 替换服务响应。覆盖四种适配器的补全请求，以及 DeepSeek、Anthropic、Google 的工具循环和推理元数据回传。
- 覆盖自动工具与结构化输出、有序通知与接收等待、隐藏工具、通知失败、格式修复、参数校验、调用去重、工具失败、取消、超时和次数上限。
- 真实 DeepSeek V4 Pro：普通补全成功；自动循环两次模型请求，时间工具执行一次且结构化台词校验通过；受控循环时间工具执行一次，观测到 `reasoning → running → execute → succeeded → 最终台词返回`，总计五次成功模型请求。真实验证通过 `--mode=all` 的断言，未要求厂商必须返回 reasoning。
- 实测发现：思考模式强制指定工具返回 HTTP 400，说明为 `Thinking mode does not support this tool_choice`。demo 使用 `auto` 并核对实际执行次数，不把“请求成功”当作“工具执行成功”。
- DeepSeek 的 `Output.object` 使用兼容模式：SDK 把 Schema 加入提示并请求 JSON 输出，再本地校验，不等同于厂商原生严格 Schema 保证。

Anthropic、Google 和自定义兼容服务仅完成协议夹具验证，尚无真实凭据实测。demo 未实现完整 `GenerationRequest`：历史预算、摘要、附件、过渡台词、提醒和续聊输入仍需后续设计；`summary` 固定返回 `null`。

## 参考

- [AI SDK 供应商](https://ai-sdk.dev/providers/ai-sdk-providers)
- [工具调用](https://ai-sdk.dev/docs/ai-sdk-core/tools-and-tool-calling)
- [结构化输出](https://ai-sdk.dev/docs/ai-sdk-core/generating-structured-data)
- [JSON Schema 验证器](https://ai-sdk.dev/docs/reference/ai-sdk-core/json-schema)
- [DeepSeek 思考模式与工具历史](https://api-docs.deepseek.com/guides/thinking_mode/)
