import path from 'node:path'
import { fileURLToPath } from 'node:url'
import nunjucks from 'nunjucks'
import Ajv from 'ajv'
import { generateText, jsonSchema } from 'ai'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { createDeepSeek } from '@ai-sdk/deepseek'
import { uuid, copy, requireValue, failure, problem } from '../core/support.js'

/**
 * @typedef {object} AgentCharacter
 * @property {string} id 角色 ID。
 * @property {string} formId 当前形态 ID。
 * @property {string} displayName 角色展示名称。
 * @property {string} description 角色人设与语气描述。
 * @property {Pick<import('../../shared/contracts/presentation.js').PerformanceCatalog, 'motions' | 'expressions'>} performances 可供模型选择的普通动作与表情目录。
 */

/** @typedef {Pick<import('../../shared/contracts/conversation.js').CharacterLine, 'id' | 'speakerId' | 'formId' | 'text' | 'translation' | 'emotion' | 'performance'>} ContextLine 用于构建历史上下文的角色台词摘要。 */

/**
 * 用于构建模型提示词的单条上下文消息。
 * @typedef {{ messageId: string } & ({ kind: 'user', source: import('../../shared/contracts/conversation.js').UserMessage['source'], text: string,
 *   attachments: import('../../shared/contracts/conversation.js').Attachment[], unavailableAttachments: string[] }
 *   | { kind: 'assistant', parts: Array<import('../../shared/contracts/conversation.js').ReasoningPart
 *       | import('../../shared/contracts/conversation.js').ToolCallPart | { kind: 'dialogue', lines: ContextLine[] }>, protocol: import('../../shared/contracts/common.js').Metadata }
 *   | { kind: 'tool-result', callId: string, toolName: string, outcome: import('../../shared/contracts/conversation.js').ToolOutcome }
 *   | { kind: 'abstract', text: string })} ContextMessage
 */

/** @typedef {Extract<ContextMessage, { kind: 'user' }>} ContextUserMessage 用户输入消息的上下文结构。 */

/**
 * 用于构建模型上下文的单个对话轮次。
 * @typedef {object} ContextTurn
 * @property {string} turnId 轮次 ID。
 * @property {ContextMessage[]} messages 该轮次内的有序消息列表。
 */

/** @typedef {Required<Omit<import('../../shared/contracts/settings.js').GenerationOptions, 'connectionId' | 'modelId'>>} ResolvedGenerationOptions 已合并默认值的完整生成配置。 */

/**
 * @typedef {object} GeneratedLine
 * @property {string} id 台词唯一 ID。
 * @property {string} speakerId 发言角色 ID。
 * @property {string} formId 发言角色的形态 ID。
 * @property {string} text 台词正文。
 * @property {string | null} translation 台词翻译。
 * @property {string | null} emotion 情绪标签。
 * @property {import('../../shared/contracts/presentation.js').PerformanceSelection} performance 模型选择的动作与表情。
 */

/**
 * 单次模型响应的内容增量通知（包含思考过程、工具调用或最终台词）。
 * @typedef {object} ResponseUpdate
 * @property {string} responseId 本次模型响应的唯一 ID（对应 AssistantMessage.id）。
 * @property {Array<import('../../shared/contracts/conversation.js').ReasoningPart
 *   | import('../../shared/contracts/conversation.js').ToolCallPart | { id: string, kind: 'dialogue', lines: GeneratedLine[] }>} appendParts 本次追加到该响应中的内容片段。
 * @property {import('../../shared/contracts/common.js').Metadata} protocol 附加协议元数据。
 */

/**
 * @typedef {object} GenerationRequest
 * @property {string} turnId 当前对话轮次 ID。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 对话编排模式（单角色聊天或小剧场）。
 * @property {AgentCharacter[]} characters 本轮参与的角色信息列表。
 * @property {{ displayName: string, description: string } | null} userPersona 当前用户对话身份的名称与描述。
 * @property {ContextTurn[]} history 当前轮次之前的历史对话上下文。
 * @property {{ kind: 'message', message: ContextUserMessage } | { kind: 'scenario' }} input 本轮触发输入（用户消息或小剧场情景指令）。
 * @property {import('../../shared/contracts/settings.js').ScenarioSettings | null} scenario 小剧场情景设定。
 * @property {ResolvedGenerationOptions} options 本轮使用的生成参数配置。
 * @property {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 本轮使用的模型连接与能力快照。
 * @property {string[]} supplementalContext 额外补充到系统提示词中的背景知识列表。
 * @property {import('../../shared/contracts/tools.js').AgentTool[]} tools 本轮允许模型调用的工具列表。
 */

/**
 * 生成过程中的事件接收器，用于按顺序保存模型响应片段和工具执行结果。
 * @typedef {object} GenerationObserver
 * @property {(update: ResponseUpdate) => Promise<void>} onResponse 接收模型输出的思考过程、工具调用或台词。
 * @property {(result: import('../../shared/contracts/conversation.js').ToolResultMessage) => Promise<void>} onToolResult 接收单次工具调用的执行结果。
 */

/**
 * @typedef {object} TokenUsage
 * @property {number | null} inputTokens 输入 Token 消耗量（服务商未返回时为 null）。
 * @property {number | null} outputTokens 输出 Token 消耗量。
 * @property {number | null} reasoningTokens 推理思考 Token 消耗量。
 */

/**
 * @typedef {object} ContextInspection
 * @property {number | null} estimatedInputTokens 预估的输入 Token 数量（包含附件等无法估算内容时为 null）。
 * @property {number} reservedOutputTokens 为模型输出预留的 Token 预算。
 * @property {number | null} modelLimit 模型支持的最大上下文 Token 上限。
 * @property {string | null} limitSource 上下文上限数值的来源说明。
 * @property {'fits' | 'over-budget' | 'unknown'} fit 当前上下文是否能装入预算范围。
 * @property {{ enabled: boolean, abstractMessageId: string | null, retainedTurns: number }} compression 当前的上下文压缩配置状态。
 * @property {TokenUsage | null} lastUsage 最近一次实际请求的 Token 消耗记录。
 * @property {import('../../shared/contracts/common.js').Problem[]} problems 检查过程中发现的问题列表。
 */

/**
 * @typedef {object} CompressionRequest
 * @property {ContextTurn[]} history 待压缩的历史轮次与旧摘要列表。
 * @property {ResolvedGenerationOptions} options 生成参数配置。
 * @property {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 模型连接快照。
 */

/** @typedef {Omit<GenerationRequest, 'turnId' | 'input'> & { input: GenerationRequest['input'] | null }} ContextInspectionRequest 上下文预算预览请求参数（input 为 null 时仅检查已有历史与配置）。 */

/**
 * LLM 生成与工具调用模块，负责渲染提示词模板、驱动模型多步工具调用循环、校验输出格式以及生成历史摘要。
 */
export class Agent {
  /**
   * 创建一个 Agent 实例。
   * @param {object} options 创建选项。
   * @param {(scope: string) => string} options.credential 根据临时凭据作用域 ID 获取 API 密钥的函数。
   * @param {typeof generateText} [options.generate] 底层文本生成函数（默认使用 AI SDK 的 generateText，测试时可替换）。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 媒体资源管理器，用于读取内联发送的图片和文件附件。
   * @param {string} [options.templateDirectory] Nunjucks 提示词模板所在的目录路径。
   */
  constructor({
    credential,
    generate = generateText,
    assets,
    templateDirectory = path.join(path.dirname(fileURLToPath(import.meta.url)), 'prompts/templates')
  }) {
    this.credential = credential
    this.assets = assets
    this.generate = async (options) => {
      try {
        return await generate(options)
      } catch (error) {
        if (options.abortSignal?.aborted) {
          if (options.abortSignal.reason?.name === 'TimeoutError')
            throw failure('timeout', '模型请求超时。', true)
          throw new DOMException('请求已取消', 'AbortError')
        }
        const cause = error.lastError ?? error
        const status = cause.statusCode
        if (status === 401 || status === 403) throw failure('authentication', '模型连接鉴权失败。')
        if (cause.data?.error?.code === 'insufficient_quota')
          throw failure('quota_exhausted', '模型连接额度不足。')
        if (status === 429) throw failure('rate_limit', '模型服务限流。', true)
        if (status >= 500) throw failure('provider_unavailable', '模型服务暂时不可用。', true)
        if (cause.problem) throw cause
        throw failure('model_request_failed', '模型请求失败，请检查连接与输入能力。')
      }
    }
    this.templates = new nunjucks.Environment(
      new nunjucks.FileSystemLoader(templateDirectory, { watch: false, noCache: false }),
      { autoescape: false, throwOnUndefined: true, trimBlocks: true, lstripBlocks: true }
    )
    this.validator = new Ajv({ allErrors: true, strict: false })
    for (const name of ['system.njk', 'output.njk', 'scenario.njk', 'summary.njk'])
      this.templates.getTemplate(name, true)
  }

  /**
   * 根据解析后的模型连接快照创建对应的 AI SDK 模型实例。
   * @param {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 已解析的模型连接快照。
   * @returns {import('ai').LanguageModel} AI SDK 语言模型实例。
   */
  model(connection) {
    const options = {
      apiKey: this.credential(connection.credentialScope),
      baseURL: connection.baseUrl,
      name: connection.provider
    }
    if (connection.provider === 'deepseek') return createDeepSeek(options)(connection.modelId)
    requireValue(connection.provider === 'openai-compatible', 'unsupported_provider')
    requireValue(
      ['chat', 'chat-completions', 'openai-compatible'].includes(connection.protocol),
      'unsupported_protocol'
    )
    return createOpenAICompatible(options).chatModel(connection.modelId)
  }

  /**
   * 渲染系统提示词（System Prompt）。
   * 角色描述和人设仅作为普通变量插入模板，不对其进行二次模板渲染，防止角色描述中含有的模板语法被意外执行。
   */
  system(request) {
    return this.templates.render('system.njk', {
      mode: request.mode,
      language: request.options.language,
      translationLanguage: request.options.translationLanguage ?? '无',
      characters: JSON.stringify(request.characters),
      persona: JSON.stringify(request.userPersona),
      knowledge: JSON.stringify(request.supplementalContext)
    })
  }

  /**
   * 将请求中的历史轮次与当前输入转换为 AI SDK 所需的消息数组。
   */
  async messages(request, inspect = false) {
    const messages = []
    for (const turn of request.history)
      for (const message of turn.messages) {
        if (message.kind === 'abstract')
          messages.push({ role: 'user', content: '历史累计摘要：' + message.text })
        if (message.kind === 'user')
          messages.push({
            role: 'user',
            content: await this.userContent(message, request.connection, inspect)
          })
        if (message.kind === 'assistant') {
          const content = []
          for (const part of message.parts) {
            if (part.kind === 'reasoning') content.push({ type: 'reasoning', text: part.text })
            if (part.kind === 'dialogue')
              content.push({ type: 'text', text: JSON.stringify({ lines: part.lines }) })
            if (part.kind === 'tool-call')
              content.push({
                type: 'tool-call',
                toolCallId: part.callId,
                toolName: part.toolName,
                input: part.arguments
              })
          }
          if (content.length) messages.push({ role: 'assistant', content })
        }
        if (message.kind === 'tool-result')
          messages.push({
            role: 'tool',
            content: [
              {
                type: 'tool-result',
                toolCallId: message.callId,
                toolName: message.toolName,
                output: { type: 'json', value: message.outcome }
              }
            ]
          })
      }
    if (request.input?.kind === 'message')
      messages.push({
        role: 'user',
        content: await this.userContent(request.input.message, request.connection, inspect)
      })
    if (request.scenario)
      messages.push({
        role: 'user',
        content: this.templates.render('scenario.njk', {
          scenario: JSON.stringify(request.scenario)
        })
      })
    if (request.input?.kind === 'scenario')
      messages.push({ role: 'user', content: '请按当前情景开始角色对话。' })
    return messages
  }

  /**
   * 组装单条用户消息的文本与附件内容，并校验当前模型连接是否支持相应类型的附件输入。
   */
  async userContent(message, connection, inspect) {
    const content = [{ type: 'text', text: message.text || '（附件）' }]
    for (const attachment of message.attachments ?? []) {
      const info = await this.assets.inspect(attachment.asset)
      const image = info.mediaType.startsWith('image/')
      const capability = image
        ? connection.capabilities.imageInput
        : connection.capabilities.fileInput
      requireValue(
        capability?.status === 'supported' && connection.attachmentTransport === 'inline',
        'unsupported_input',
        '当前连接未声明支持此附件输入。'
      )
      // 预览上下文预算（inspect 为 true）时使用空字节数组占位，避免为了估算 Token 而把所有附件文件真正读入内存。
      const data = inspect ? new Uint8Array(0) : await this.assets.read(attachment.asset)
      content.push(
        image
          ? { type: 'image', image: data, mediaType: info.mediaType }
          : { type: 'file', data, mediaType: info.mediaType, filename: info.name }
      )
    }
    return content
  }

  /**
   * 将请求中的生成选项转换为 AI SDK 调用参数。
   */
  parameters(request, signal) {
    const options = request.options
    requireValue(Number.isInteger(options.timeoutMs) && options.timeoutMs > 0)
    const signals = [AbortSignal.timeout(options.timeoutMs)]
    if (signal) signals.push(signal)
    const reasoning = options.reasoning
    let providerOptions
    if (reasoning.enabled) {
      requireValue(
        request.connection.capabilities.reasoning?.status === 'supported',
        'unsupported_reasoning'
      )
      if (reasoning.effort)
        requireValue(
          request.connection.capabilities.reasoningEfforts?.includes(reasoning.effort),
          'unsupported_reasoning'
        )
    }
    if (request.connection.provider === 'deepseek')
      providerOptions = {
        deepseek: {
          thinking: { type: reasoning.enabled ? 'enabled' : 'disabled' },
          ...(reasoning.effort ? { reasoningEffort: reasoning.effort } : {})
        }
      }
    else if (reasoning.enabled) {
      requireValue(reasoning.effort, 'unsupported_reasoning', '兼容协议需要明确的推理强度。')
      providerOptions = { 'openai-compatible': { reasoningEffort: reasoning.effort } }
    }
    return {
      model: this.model(request.connection),
      providerOptions,
      maxOutputTokens: options.maxOutputTokens,
      temperature: options.temperature,
      topP: options.topP,
      maxRetries: options.limits.maxRequestRetries,
      abortSignal: AbortSignal.any(signals)
    }
  }

  /**
   * 解析并校验模型输出的 JSON 台词结构，确保发言角色、形态、动作和表情均在本轮允许的范围内。
   */
  lines(text, request) {
    let output
    try {
      output = JSON.parse(text)
    } catch {
      throw failure('invalid_output', '模型未返回合法 JSON。')
    }
    requireValue(
      Array.isArray(output.lines) && output.lines.length > 0 && output.lines.length <= 100,
      'invalid_output'
    )
    return output.lines.map((line) => {
      const character = request.characters.find(
        (item) => item.id === line.speakerId && item.formId === line.formId
      )
      requireValue(
        character && typeof line.text === 'string' && line.text.trim().length > 0,
        'invalid_output'
      )
      requireValue(
        line.translation === null || typeof line.translation === 'string',
        'invalid_output'
      )
      requireValue(line.emotion === null || typeof line.emotion === 'string', 'invalid_output')
      const performance = line.performance ?? { motion: 'auto', expression: 'auto' }
      for (const [key, catalog] of [
        ['motion', character.performances.motions],
        ['expression', character.performances.expressions]
      ]) {
        requireValue(
          performance[key] === 'auto' || catalog.some((option) => option.id === performance[key]),
          'invalid_output'
        )
      }
      return {
        id: uuid(),
        speakerId: line.speakerId,
        formId: line.formId,
        text: line.text,
        translation: line.translation,
        emotion: line.emotion,
        performance: copy(performance)
      }
    })
  }

  /**
   * 执行一轮完整的模型生成与工具调用循环，并通过 observer 逐步回调产出的思考、工具调用及角色台词。
   * @param {GenerationRequest} request 本轮生成的完整输入与配置参数。
   * @param {GenerationObserver} observer 按顺序接收响应片段和工具结果的回调对象。
   * @param {AbortSignal} [signal] 控制本轮生成的取消信号。
   * @returns {Promise<{ status: 'completed' | 'cancelled', usage: TokenUsage | null }>} 生成循环的完成状态与最近一次模型请求的 Token 消耗。
   */
  async run(request, observer, signal) {
    const inspection = await this.inspectContext(request)
    requireValue(inspection.fit !== 'over-budget', 'context_over_budget')
    const messages = await this.messages(request)
    const system = this.system(request)
    const tools = Object.fromEntries(
      request.tools.map((tool) => [
        tool.name,
        { description: tool.description, inputSchema: jsonSchema(tool.parametersSchema) }
      ])
    )
    let repairs = 0
    let usage = null
    const callIds = new Set()
    for (let step = 0; step < request.options.limits.maxSteps; step += 1) {
      signal?.throwIfAborted()
      const response = await this.generate({
        ...this.parameters(request, signal),
        system,
        messages,
        tools
      })
      usage = {
        inputTokens: response.usage?.inputTokens ?? null,
        outputTokens: response.usage?.outputTokens ?? null,
        reasoningTokens: response.usage?.reasoningTokens ?? null
      }
      const responseId = uuid()
      const parts = []
      if (response.reasoningText)
        parts.push({ id: uuid(), kind: 'reasoning', text: response.reasoningText, protocol: {} })
      const calls = response.toolCalls ?? []
      for (const call of calls) {
        requireValue(!callIds.has(call.toolCallId), 'duplicate_tool_call')
        callIds.add(call.toolCallId)
        parts.push({
          id: uuid(),
          kind: 'tool-call',
          callId: call.toolCallId,
          toolName: call.toolName,
          arguments: copy(call.input),
          protocol: {}
        })
      }
      // 为什么在真正执行工具前先把 tool-call 记录通知给 observer 保存：
      // 若工具执行中途进程崩溃或被用户取消，对话存档中必须先有对应的 tool-call 记录，后续才能为其补记中断状态并保持历史可追溯。
      if (parts.length) await observer.onResponse({ responseId, appendParts: parts, protocol: {} })
      if (calls.length) {
        const content = calls.map((call) => ({
          type: 'tool-call',
          toolCallId: call.toolCallId,
          toolName: call.toolName,
          input: call.input
        }))
        if (response.reasoningText)
          content.unshift({ type: 'reasoning', text: response.reasoningText })
        messages.push({ role: 'assistant', content })
        const seen = new Set()
        for (const call of calls) {
          requireValue(!seen.has(call.toolCallId), 'invalid_output')
          seen.add(call.toolCallId)
          const tool = request.tools.find((item) => item.name === call.toolName)
          let outcome
          try {
            requireValue(
              tool && this.validator.compile(tool.parametersSchema)(call.input),
              'invalid_tool_input'
            )
            outcome = await tool.execute(
              copy(call.input),
              { turnId: request.turnId, assistantMessageId: responseId, callId: call.toolCallId },
              signal
            )
            requireValue(
              tool.mode !== 'async' || ['accepted', 'failed'].includes(outcome.status),
              'invalid_tool_result'
            )
          } catch (error) {
            if (signal?.aborted) throw error
            outcome = { status: 'failed', problem: problem(error) }
          }
          await observer.onToolResult({
            id: uuid(),
            kind: 'tool-result',
            assistantMessageId: responseId,
            callId: call.toolCallId,
            origin: 'tool',
            outcome,
            extensions: {}
          })
          messages.push({
            role: 'tool',
            content: [
              {
                type: 'tool-result',
                toolCallId: call.toolCallId,
                toolName: call.toolName,
                output: { type: 'json', value: outcome }
              }
            ]
          })
        }
      }
      if (response.text?.trim()) {
        let lines
        try {
          lines = this.lines(response.text, request)
        } catch (error) {
          if (repairs >= request.options.limits.maxFormatRepairs) throw error
          // 为什么格式错误时向当前 messages 追加提示继续循环，而不是从头重新发起整轮生成：
          // 之前的步骤中可能已经执行过具有副作用的工具调用，保留已有工具结果并仅要求模型修正 JSON 格式，可避免重复执行工具。
          repairs += 1
          messages.push({
            role: 'user',
            content:
              '上一响应的台词格式不合法。请按系统规定的 JSON 格式重新输出台词，不要重复执行工具。'
          })
          continue
        }
        await observer.onResponse({
          responseId,
          appendParts: [{ id: uuid(), kind: 'dialogue', lines }],
          protocol: {}
        })
        if (!calls.length) return { status: 'completed', usage }
        messages.push({ role: 'assistant', content: response.text })
      } else if (!calls.length) throw failure('empty_response', '模型返回空响应。')
    }
    throw failure('step_limit', '本轮工具循环达到上限。')
  }

  /**
   * 预估给定生成请求的上下文 Token 占用，并检查是否超出预算或存在附件能力不兼容问题。
   * @param {ContextInspectionRequest} request 待检查的上下文与生成配置。
   * @returns {Promise<ContextInspection>} 上下文 Token 预估结果与能力诊断信息。
   */
  async inspectContext(request) {
    const messages = await this.messages(request, true)
    const material = JSON.stringify({
      system: this.system(request),
      messages,
      tools: request.tools.map((tool) => ({
        name: tool.name,
        description: tool.description,
        parametersSchema: tool.parametersSchema
      }))
    })
    const hasAttachments = [
      ...request.history.flatMap((turn) => turn.messages),
      request.input?.message
    ].some((message) => message?.attachments?.length)
    const estimatedInputTokens = hasAttachments
      ? null
      : Math.ceil(Buffer.byteLength(material, 'utf8') / 3)
    const modelLimit = request.connection.capabilities.contextTokens ?? null
    const budget = Math.min(modelLimit ?? Infinity, request.options.contextTokenBudget)
    let fit = 'unknown'
    if (estimatedInputTokens !== null)
      fit =
        estimatedInputTokens + request.options.maxOutputTokens <= budget ? 'fits' : 'over-budget'
    return {
      estimatedInputTokens,
      reservedOutputTokens: request.options.maxOutputTokens,
      modelLimit,
      limitSource: request.connection.capabilities.contextSource ?? null,
      fit,
      compression: {
        enabled: request.options.compression.enabled,
        abstractMessageId: null,
        retainedTurns: request.options.compression.retainedTurns
      },
      lastUsage: null,
      problems: []
    }
  }

  /**
   * 调用模型将指定的历史对话轮次与旧摘要压缩为一段新的累计摘要文本。
   * @param {CompressionRequest} request 待压缩的历史轮次、生成配置与模型连接。
   * @param {AbortSignal} [signal] 控制本次压缩请求的取消信号。
   * @returns {Promise<{ text: string, usage: TokenUsage | null }>} 生成的摘要文本与 Token 消耗记录。
   */
  async compress(request, signal) {
    const response = await this.generate({
      ...this.parameters(request, signal),
      system: this.templates.render('summary.njk', {}),
      prompt: JSON.stringify(request.history)
    })
    requireValue(response.text?.trim(), 'empty_response')
    return {
      text: response.text.trim(),
      usage: {
        inputTokens: response.usage?.inputTokens ?? null,
        outputTokens: response.usage?.outputTokens ?? null,
        reasoningTokens: response.usage?.reasoningTokens ?? null
      }
    }
  }
}
