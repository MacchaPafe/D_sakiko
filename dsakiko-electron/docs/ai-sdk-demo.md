# AI SDK 使用与接口验证

AI SDK 可以统一厂商请求、原生工具调用和结构化输出。项目仍需控制通知顺序、工具副作用、取消及业务校验。独立 demo 位于 [`scripts/demos/ai-sdk`](../scripts/demos/ai-sdk/)，依赖在自己的 `package.json` 和 `package-lock.json` 中锁定，不接入现有应用启动流程。

## 运行

使用 Node 22.12 以上版本，本次验证使用 Node 24.21.0、`ai@7.0.130`。

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

厂商提供默认地址，用户仍需选择模型；自定义兼容服务还需 `baseURL`。模型列表、能力声明、连接管理及设置界面由应用维护。`providerOptions` 用于经过验证的厂商专属参数，SDK 不保证所有模型支持相同能力。

工具参数沿用现有 `AgentTool.parametersSchema`。**`jsonSchema(schema)` 默认不执行参数校验**，demo 通过 Ajv 编译 Schema，再提供给 `jsonSchema(schema, { validate })`；也可以直接使用带验证能力的 Zod Schema。`execute` 和密钥都留在 Node，不进入共享协议。

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

- 离线协议与边界测试：33 项通过。使用真实 SDK 和厂商适配器，仅以可控 `fetch` 替换服务响应。覆盖四种适配器的补全请求，以及 DeepSeek、Anthropic、Google 的工具循环和推理元数据回传。
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
