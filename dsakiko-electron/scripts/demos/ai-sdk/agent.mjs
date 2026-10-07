import { generateText, jsonSchema, tool } from 'ai'
import Ajv from 'ajv'
import { isDeepStrictEqual } from 'node:util'
import { validateReply } from './schema.mjs'

/** @typedef {import('../../../src/backend/llm/interface.js').AgentTool} AgentTool */
/** @typedef {import('../../../src/backend/llm/interface.js').GenerationObserver} GenerationObserver */

/**
 * JSON Schema 需要显式验证器；jsonSchema(schema) 本身不校验参数。
 * @param {object} schema 工具的 JSON Schema。
 * @returns {import('ai').Schema} SDK 可执行校验的 Schema。
 */
export function validatedSchema(schema) {
  const validate = new Ajv({ strict: true }).compile(schema)
  return jsonSchema(schema, {
    validate(value) {
      if (validate(value)) return { success: true, value }
      return { success: false, error: new Error('工具参数不符合 JSON Schema。') }
    }
  })
}

function registerTools(tools) {
  const registry = Object.create(null)
  for (const definition of tools) {
    if (Object.hasOwn(registry, definition.name)) throw new Error('工具名称重复。')
    // 不注册 execute：先交付完整 reasoning 和 running，接受后才允许副作用。
    registry[definition.name] = tool({
      description: definition.description,
      inputSchema: validatedSchema(definition.parametersSchema)
    })
  }
  return registry
}

function toolResult(call, result, failed = false) {
  return {
    type: 'tool-result',
    toolCallId: call.toolCallId,
    toolName: call.toolName,
    output: failed
      ? { type: 'error-text', value: '工具执行失败。' }
      : { type: 'json', value: result }
  }
}

async function notifyTool(observer, definition, record, signal) {
  if (definition.visibility === 'visible') await observer.onToolActivity(structuredClone(record))
  signal.throwIfAborted()
}

async function executeTool(call, definition, observer, signal, completed) {
  const previous = completed.get(call.toolCallId)
  if (previous) {
    if (previous.toolName !== call.toolName || !isDeepStrictEqual(previous.input, call.input)) {
      throw new Error('同一 callId 不能绑定不同的工具或参数。')
    }
    return previous.output
  }
  const record = {
    callId: call.toolCallId,
    toolName: call.toolName,
    arguments: call.input,
    status: 'running',
    result: null,
    error: null
  }
  await notifyTool(observer, definition, record, signal)
  let result
  let failed = false
  try {
    result = await definition.execute(structuredClone(call.input), signal)
  } catch {
    failed = true
  }
  if (signal.aborted) {
    if (definition.visibility === 'visible') {
      await observer.onToolActivity({ ...record, status: 'interrupted' })
    }
    signal.throwIfAborted()
  }
  const output = toolResult(call, result, failed)
  // 先记住执行结果，再通知终态；终态接收失败不能重新执行副作用。
  completed.set(call.toolCallId, {
    toolName: call.toolName,
    input: structuredClone(call.input),
    output
  })
  const terminal = failed
    ? {
        ...record,
        status: 'failed',
        error: { code: 'tool_failed', message: '工具执行失败。', retryable: false }
      }
    : { ...record, status: 'succeeded', result }
  await notifyTool(observer, definition, terminal, signal)
  return output
}

/**
 * 有界循环：真实 SDK 负责报文和参数解析，项目负责通知顺序和副作用。
 * 只验证工具与台词，不实现历史预算、摘要、附件和过渡台词。
 * @param {object} options 演示输入。
 * @param {import('ai').LanguageModel} options.model 固定连接的 SDK 模型。
 * @param {import('../../../src/backend/llm/interface.js').AgentCharacter[]} options.characters 角色快照。
 * @param {AgentTool[]} options.tools 本轮工具。
 * @param {string} options.prompt 用户输入。
 * @param {GenerationObserver} options.observer 接受运行记录的调用方。
 * @param {AbortSignal} options.signal 取消信号。
 * @param {string} [options.requireTool] 第一轮强制指定工具，仅限支持该参数的模型。
 * @param {number} [options.maxSteps] 模型请求总上限，包含格式修复。
 * @param {number} [options.maxRepairs] 格式修复上限。
 * @param {number} [options.timeoutMs] 整轮超时，包含 Observer 和工具。
 * @param {object} [options.providerOptions] 厂商原生参数。
 * @returns {Promise<import('../../../src/backend/llm/interface.js').GenerationResult>} 最终台词或取消。
 */
export async function runAgent({
  model,
  characters,
  tools,
  prompt,
  observer,
  signal,
  requireTool,
  maxSteps = 8,
  maxRepairs = 2,
  timeoutMs = 60000,
  providerOptions
}) {
  const registry = registerTools(tools)
  const deadline = new AbortController()
  const timer = setTimeout(() => deadline.abort(new Error('本轮生成超时。')), timeoutMs)
  const runSignal = AbortSignal.any([signal, deadline.signal])
  const messages = [{ role: 'user', content: prompt }]
  const completed = new Map()
  let repairs = 0
  const instructions = `你为指定角色回复，不替用户发言。角色：${JSON.stringify(characters)}。
需要时间时使用工具。工具请求走原生工具调用，不写入正文。
最终正文只输出 JSON：{"lines":[{"speakerId":"sakiko","text":"一句完整中文。","translation":null,"emotion":null,"performance":{"motion":"auto","expression":"auto"}}]}。
最终 JSON 必须包含上述全部字段；speakerId 来自角色，演出使用该角色目录编号或 auto。`
  try {
    for (let step = 0; step < maxSteps; step += 1) {
      runSignal.throwIfAborted()
      let toolChoice = 'auto'
      if (repairs > 0) toolChoice = undefined
      else if (step === 0 && requireTool) toolChoice = { type: 'tool', toolName: requireTool }
      const response = await generateText({
        model,
        instructions,
        messages,
        // 修复时完全移除工具，同时本地拒绝违规调用；不依赖 tool_choice=none。
        tools: repairs > 0 ? undefined : registry,
        toolChoice,
        providerOptions,
        // 网络重试与格式修复分开；不让 SDK 隐式增加请求次数。
        maxRetries: 0,
        maxOutputTokens: 4096,
        abortSignal: runSignal,
        telemetry: { isEnabled: false }
      })
      runSignal.throwIfAborted()
      // 保留 SDK response.messages 内的分组及厂商元数据，不用展示文本重建工具历史。
      messages.push(...response.response.messages)
      for (const part of response.content) {
        if (part.type === 'reasoning' && part.text.trim()) {
          await observer.onIntermediate([{ kind: 'reasoning', text: part.text }])
          runSignal.throwIfAborted()
        }
      }
      if (response.toolCalls.length) {
        if (repairs > 0) throw new Error('格式修复期间禁止重新调用工具。')
        // 所有调用先校验，再执行；第二个非法调用不会让第一个先产生副作用。
        for (const call of response.toolCalls) {
          if (call.invalid || !Object.hasOwn(registry, call.toolName)) {
            throw new Error('模型返回未知工具或无效工具参数。')
          }
        }
        const results = []
        for (const call of response.toolCalls) {
          const definition = tools.find((item) => item.name === call.toolName)
          results.push(await executeTool(call, definition, observer, runSignal, completed))
        }
        messages.push({ role: 'tool', content: results })
        continue
      }
      let lines
      try {
        lines = validateReply(JSON.parse(response.text), characters)
      } catch {
        if (repairs >= maxRepairs) throw new Error('台词格式修复次数已用尽。')
        repairs += 1
        messages.push({
          role: 'user',
          content:
            '上一条台词 JSON 无效。请按指定结构修复，检查角色及演出编号。已有工具结果仍有效，禁止重新调用工具。'
        })
        continue
      }
      runSignal.throwIfAborted()
      return { status: 'completed', lines, summary: null }
    }
    throw new Error('模型请求步数已用尽。')
  } catch (error) {
    if (signal.aborted) return { status: 'cancelled' }
    if (deadline.signal.aborted) throw new Error('本轮生成超时。')
    throw error
  } finally {
    clearTimeout(timer)
  }
}
