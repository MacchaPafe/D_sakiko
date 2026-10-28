import path from 'node:path'
import { characterContext, dialogueProjection } from './context.js'
import { fileURLToPath } from 'node:url'
import nunjucks from 'nunjucks'
import Ajv from 'ajv'
import { generateText, jsonSchema } from 'ai'
import { createModel, reasoningOptions } from './adapters.js'
import { AttachmentPreparation } from '../remote-files/preparation.js'
import { rejectedRemoteFiles } from '../remote-files/adapter.js'
import { normalizeConnection } from './connections.js'
import { contextBudget } from './context-budget.js'
import {
  responseContent,
  responseOptions,
  restoreReplay,
  saveReplay,
  semanticHistory,
  turnFingerprint
} from './replay.js'
import { uuid, copy, requireValue, failure, problem } from '../core/support.js'

/**
 * @typedef {object} AgentCharacter
 * @property {string} id 角色 ID。
 * @property {string} formId 当前形态 ID。
 * @property {string} name 跨形态角色名。
 * @property {string} displayName 当前形态名。
 * @property {Array<{id: string, displayName: string, description: string}>} forms 全部形态的人物资料。
 * @property {'v2' | 'v3' | null} modelVersion 当前模型格式。
 * @property {string} description 角色人设与语气描述。
 * @property {Pick<import('../../shared/contracts/presentation.js').PerformanceCatalog, 'motions' | 'expressions' | 'presets'>} performances 可供模型选择的普通动作、表情与组合参考。
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
 * @property {import('./context.js').CharacterContext | null} [characterContext] 该轮人物资料。
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
 * @property {string[]} supplementalContext 额外补充到本轮控制消息中的背景知识列表。
 * @property {import('../../shared/contracts/tools.js').AgentTool[]} tools 本轮允许模型调用的工具列表。
 * @property {import('../../shared/contracts/tools.js').ToolDefinition[]} [toolDefinitions] 固定能力目录；声明存在不代表本轮获准执行。
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
 * @property {number} effectiveBudget 模型已知窗口或未知时的应用兜底预算。
 * @property {number} thresholdTokens 本轮有效摘要阈值。
 * @property {string | null} limitSource 上下文上限数值的来源说明。
 * @property {'fits' | 'over-budget' | 'unknown'} fit 当前上下文是否能装入预算范围。
 * @property {{ enabled: boolean, abstractMessageId: string | null, retainedTurns: number }} compression 当前的上下文压缩配置状态。
 * @property {TokenUsage | null} lastUsage 最近一次实际请求的 Token 消耗记录。
 * @property {import('../../shared/contracts/common.js').Problem[]} problems 检查过程中发现的问题列表。
 * @property {Array<{assetId: string, transport: string, reason: string}>} attachmentDecisions 附件传送选择，不包含厂商引用或凭据。
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
   * @param {typeof fetch} [options.fetch] 可替换的 HTTP 传输，用于协议离线验证。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 媒体资源管理器，用于读取内联发送的图片和文件附件。
   * @param {import('../remote-files/interface.js').RemoteFiles} [options.remoteFiles] 后端远端文件管理。
   * @param {string} [options.templateDirectory] Nunjucks 提示词模板所在的目录路径。
   */
  constructor({
    credential,
    generate = generateText,
    fetch: transport = fetch,
    assets,
    remoteFiles,
    templateDirectory = path.join(path.dirname(fileURLToPath(import.meta.url)), 'prompts/templates')
  }) {
    this.credential = credential
    this.assets = assets
    this.transport = transport
    this.attachments = new AttachmentPreparation({ assets, remoteFiles, credential, transport })
    this.generate = async (options) => {
      try {
        const result = await generate(options)
        const unsupported = result.warnings?.find(
          (warning) =>
            warning.type !== 'deprecated' &&
            /reasoning|thinking|signature|tool|image|file/i.test(
              `${warning.feature ?? ''} ${warning.message ?? ''}`
            )
        )
        requireValue(
          !unsupported,
          'unsupported_model_option',
          '当前 SDK 或模型不能完整处理所选推理、工具或附件能力。'
        )
        return result
      } catch (error) {
        if (options.abortSignal?.aborted) {
          if (options.abortSignal.reason?.name === 'TimeoutError')
            throw failure('timeout', '模型请求超时。', true)
          throw new DOMException('请求已取消', 'AbortError')
        }
        const cause = error.lastError ?? error
        const remoteRejection = rejectedRemoteFiles(cause)
        if (remoteRejection) {
          const rejected = failure(
            'remote_file_rejected',
            '模型拒绝了远端附件引用，请重试生成。',
            true
          )
          rejected.remoteRejection = remoteRejection
          throw rejected
        }
        const status = cause.statusCode
        if (status === 401 || status === 403) throw failure('authentication', '模型连接鉴权失败。')
        if (cause.data?.error?.code === 'insufficient_quota')
          throw failure('quota_exhausted', '选择模型的余额不足，请充值。')
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
    for (const name of [
      'system.njk',
      'output.njk',
      'scenario.njk',
      'summary.njk',
      'character-context.njk',
      'runtime-controls.njk'
    ])
      this.templates.getTemplate(name, true)
  }

  /**
   * 根据解析后的模型连接快照创建对应的 AI SDK 模型实例。
   * @param {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 已解析的模型连接快照。
   * @returns {import('ai').LanguageModel} AI SDK 语言模型实例。
   */
  model(connection, reasoning) {
    return createModel(
      connection,
      this.credential(connection.credentialScope),
      reasoning,
      this.transport
    )
  }

  /**
   * 渲染固定系统提示词，与人物、形态、语言和模型能力无关。
   */
  system() {
    return this.templates.render('system.njk', {})
  }

  /** 生成控制消息，即向模型说明本轮该如何回复的消息。
   * 控制消息只存在于请求尾部，只会在当前轮次中存在，不会写入历史中。 */
  controls(request) {
    const characters = request.characters.map((character) => ({
      ...character,
      name: character.name ?? character.displayName
    }))
    const examples = characters.map((character) => {
      const greeting = { Japanese: 'また会えて嬉しい。', Chinese: '很高兴又见到你。' }
      const line = {
        speaker: character.name,
        text: greeting[request.options.language] ?? greeting.Chinese,
        translation: request.options.translationLanguage
          ? (greeting[request.options.translationLanguage] ?? greeting.Chinese)
          : null,
        emotion: 'happiness'
      }
      if (character.modelVersion === 'v3') line.performance = { motion: 'auto', expression: 'auto' }
      return line
    })
    return this.templates.render('runtime-controls.njk', {
      mode: request.mode,
      characters,
      language: request.options.language,
      translationLanguage: request.options.translationLanguage,
      scenario: request.scenario,
      knowledge: request.supplementalContext,
      hasTools: request.tools.length > 0,
      allowedTools: request.tools.map((tool) => tool.name).sort(),
      example: JSON.stringify({ lines: examples })
    })
  }

  /**
   * 将请求中的历史轮次与当前输入转换为 AI SDK 所需的消息数组。
   */
  async messages(request, inspect = false, signal) {
    const messages = []
    const currentContext = characterContext(request)
    let previousContext = null
    function sameContext(left, right) {
      return JSON.stringify(left) === JSON.stringify(right)
    }
    const appendContext = (context) => {
      if (sameContext(previousContext, context)) return
      messages.push({
        role: 'user',
        content: this.templates.render('character-context.njk', context)
      })
      previousContext = context
    }
    for (const [turnIndex, turn] of request.history.entries()) {
      const context = turn.characterContext ?? previousContext ?? currentContext
      const contextFingerprint = turnFingerprint(
        request.history.slice(0, turnIndex),
        context,
        turn.messages.find((message) => message.kind === 'user') ?? null
      )
      appendContext(context)
      for (const message of turn.messages) {
        if (message.kind === 'abstract')
          messages.push({ role: 'user', content: '历史累计摘要：' + message.text })
        if (message.kind === 'user')
          messages.push({
            role: 'user',
            content: await this.userContent(message, request, inspect, signal)
          })
        if (message.kind === 'assistant') {
          const replay = restoreReplay(message, request.connection, contextFingerprint)
          if (replay !== null) {
            if (replay.length)
              messages.push({
                role: 'assistant',
                content: replay,
                providerOptions: responseOptions({
                  providerMetadata: {
                    openaiCompatible: {
                      reasoningDetails:
                        message.protocol.providerOptions?.openaiCompatible?.reasoning_details
                    }
                  }
                })
              })
            continue
          }
          const content = []
          for (const part of message.parts) {
            // 旧存档只含明文推理；有来源的回传材料失效后不跨模型转发签名或推理。
            if (
              part.kind === 'reasoning' &&
              !message.protocol?.origin &&
              normalizeConnection(request.connection).protocol === 'chat-completions'
            )
              content.push({ type: 'reasoning', text: part.text })
            if (part.kind === 'dialogue')
              content.push({
                type: 'text',
                text: JSON.stringify({ lines: dialogueProjection(part.lines, context) })
              })
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
    }
    appendContext(currentContext)
    if (request.input?.kind === 'message')
      messages.push({
        role: 'user',
        content: await this.userContent(request.input.message, request, inspect, signal)
      })
    if (request.input?.kind === 'scenario')
      messages.push({ role: 'user', content: '请按当前情景开始角色对话。' })
    messages.push({ role: 'user', content: this.controls(request) })
    return messages
  }

  /**
   * 组装单条用户消息的文本与附件内容，并校验当前模型连接是否支持相应类型的附件输入。
   */
  async userContent(message, request, inspect, signal) {
    return this.attachments.content(message, request, inspect, signal)
  }

  /**
   * 将请求中的生成选项转换为 AI SDK 调用参数。
   */
  parameters(request, signal) {
    const options = request.options
    requireValue(Number.isInteger(options.timeoutMs) && options.timeoutMs > 0)
    const signals = [AbortSignal.timeout(options.timeoutMs)]
    if (signal) signals.push(signal)
    const providerOptions = reasoningOptions(request.connection, options.reasoning)
    const protocol = normalizeConnection(request.connection).protocol
    const fixedSampling =
      options.reasoning.enabled &&
      (['responses', 'anthropic-messages'].includes(protocol) ||
        ['deepseek', 'openai'].includes(request.connection.provider))
    return {
      model: this.model(request.connection, options.reasoning),
      providerOptions,
      maxOutputTokens: contextBudget(request).reservedOutputTokens,
      // 原生推理模型自行控制采样，不发送它们明确不接受的采样参数。
      ...(!fixedSampling ? { temperature: options.temperature, topP: options.topP } : {}),
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
        (item) => (item.name ?? item.displayName) === line.speaker
      )
      requireValue(
        character && typeof line.text === 'string' && line.text.trim().length > 0,
        'invalid_output'
      )
      requireValue(
        request.options.translationLanguage
          ? typeof line.translation === 'string' && line.translation.trim().length > 0
          : line.translation === null,
        'invalid_output'
      )
      requireValue(
        ['happiness', 'sadness', 'anger', 'surprise', 'fear', 'disgust', 'like'].includes(
          line.emotion
        ),
        'invalid_output'
      )
      const performance =
        character.modelVersion === 'v3'
          ? (line.performance ?? { motion: 'auto', expression: 'auto' })
          : { motion: 'auto', expression: 'auto' }
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
        speakerId: character.id,
        formId: character.formId,
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
    // 调用方更新设置不能改变正在进行中的工具循环；工具执行函数单独保留。
    const fixed = {
      ...copy({ ...request, tools: [] }),
      tools: request.tools.map((tool) => ({ ...tool }))
    }
    this.attachments.transfer(request, fixed)
    const execute = async () => {
      try {
        const result = await this.runPrepared(fixed, observer, signal)
        return { ...result, attachmentDecisions: await this.attachments.diagnostics(fixed) }
      } catch (error) {
        await this.attachments
          .invalidateRejected(fixed, error)
          .catch((failure) => this.assets.reportMaintenance?.(failure))
        throw error
      }
    }
    // 资源随实际执行结束释放，避免上层取消等待后删除仍在使用的文件。
    return this.assets.using
      ? this.assets.using({ history: fixed.history, input: fixed.input }, execute)
      : execute()
  }

  /** 已冻结请求的执行过程；上传只在此阶段发生。 */
  async runPrepared(request, observer, signal) {
    const inspection = await this.inspectContext(request)
    requireValue(inspection.fit !== 'over-budget', 'context_over_budget')
    const uploadSignals = [AbortSignal.timeout(request.options.timeoutMs)]
    if (signal) uploadSignals.push(signal)
    const uploadSignal = AbortSignal.any(uploadSignals)
    let messages
    try {
      messages = await this.messages(request, false, uploadSignal)
    } catch (error) {
      if (!signal?.aborted && uploadSignal.aborted && uploadSignal.reason?.name === 'TimeoutError')
        throw failure('timeout', '附件准备超时，本地附件已保留。', true)
      throw error
    }
    const system = this.system(request)
    const contextFingerprint = turnFingerprint(
      request.history,
      characterContext(request),
      request.input.kind === 'message' ? request.input.message : null
    )
    const tools = Object.fromEntries(
      (request.toolDefinitions ?? request.tools).map((tool) => [
        tool.name,
        { description: tool.description, inputSchema: jsonSchema(tool.parametersSchema) }
      ])
    )
    let repairs = 0
    let usage = null
    const callIds = new Set()
    for (let step = 0; step < request.options.limits.maxSteps; step += 1) {
      signal?.throwIfAborted()
      // 工具执行后只检查，不再更换历史前缀或重跑已完成的副作用。
      const current = await this.inspectContext(request, messages)
      requireValue(
        current.fit !== 'over-budget',
        'context_over_budget',
        '本轮新增工具结果或修复消息超出上下文预算，已执行记录已保留。'
      )
      const response = await this.generate({
        ...this.parameters(request, signal),
        system,
        messages,
        tools
      })
      usage = {
        inputTokens: response.usage?.inputTokens ?? null,
        outputTokens: response.usage?.outputTokens ?? null,
        reasoningTokens:
          response.usage?.outputTokenDetails?.reasoningTokens ??
          response.usage?.reasoningTokens ??
          null
      }
      const responseId = uuid()
      const content = responseContent(response)
      const messageOptions = responseOptions(response)
      const calls = content.filter((part) => part.type === 'tool-call')
      const text = content
        .filter((part) => part.type === 'text')
        .map((part) => part.text)
        .join('')
      let lines = null
      let formatError = null
      // 存在工具调用时本步仍在收集信息，不将伴随输出当作最终台词 JSON 解析。
      if (!calls.length && text.trim()) {
        try {
          lines = this.lines(text, request)
        } catch (error) {
          formatError = error
        }
      }
      const parts = []
      let dialogueAdded = false
      for (const part of content) {
        if (part.type === 'reasoning')
          parts.push({ id: uuid(), kind: 'reasoning', text: part.text, protocol: {} })
        if (part.type === 'tool-call') {
          requireValue(!callIds.has(part.toolCallId), 'duplicate_tool_call')
          callIds.add(part.toolCallId)
          parts.push({
            id: uuid(),
            kind: 'tool-call',
            callId: part.toolCallId,
            toolName: part.toolName,
            arguments: copy(part.input),
            protocol: {}
          })
        }
        if (part.type === 'text' && lines && !dialogueAdded) {
          parts.push({ id: uuid(), kind: 'dialogue', lines })
          dialogueAdded = true
        }
      }
      // 为什么在真正执行工具前先把 tool-call 记录通知给 observer 保存：
      // 若工具执行中途进程崩溃或被用户取消，对话存档中必须先有对应的 tool-call 记录，后续才能为其补记中断状态并保持历史可追溯。
      await observer.onResponse({
        responseId,
        appendParts: parts,
        protocol: saveReplay(request.connection, content, parts, contextFingerprint, messageOptions)
      })
      // 同一响应只回传一次；保留工具前的文字、空推理签名和原始块顺序。
      if (content.length)
        messages.push({ role: 'assistant', content, providerOptions: messageOptions })
      if (calls.length) {
        const results = []
        for (const call of calls) {
          const tool = request.tools.find((item) => item.name === call.toolName)
          let outcome
          try {
            requireValue(
              tool,
              'tool_disabled',
              '该工具本轮未获准使用，请勿重试；请使用允许的工具或直接回答。'
            )
            requireValue(
              this.validator.compile(tool.parametersSchema)(call.input),
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
          results.push({
            type: 'tool-result',
            toolCallId: call.toolCallId,
            toolName: call.toolName,
            output: { type: 'json', value: outcome }
          })
        }
        messages.push({ role: 'tool', content: results })
        // 工具响应中的普通说明不是最终台词 JSON；不能把它当作格式错误开启新 user 轮次。
        continue
      }
      if (lines) return { status: 'completed', usage }
      if (formatError) {
        if (repairs >= request.options.limits.maxFormatRepairs) throw formatError
        // 为什么格式错误时向当前 messages 追加提示继续循环，而不是从头重新发起整轮生成：
        // 之前的步骤中可能已经执行过具有副作用的工具调用，保留已有工具结果并仅要求模型修正 JSON 格式，可避免重复执行工具。
        repairs += 1
        messages.push({
          role: 'user',
          content:
            '上一响应的台词格式不合法。请按本轮 runtime_controls 规定的 JSON 格式重新输出台词，不要重复执行工具。'
        })
        continue
      }
      throw failure('empty_response', '模型返回空响应。')
    }
    throw failure('step_limit', '本轮工具循环达到上限。')
  }

  /**
   * 预估给定生成请求的上下文 Token 占用，并检查是否超出预算或存在附件能力不兼容问题。
   * @param {ContextInspectionRequest} request 待检查的上下文与生成配置。
   * @returns {Promise<ContextInspection>} 上下文 Token 预估结果与能力诊断信息。
   */
  async inspectContext(request, preparedMessages = null) {
    const messages = preparedMessages ?? (await this.messages(request, true))
    const material = JSON.stringify({
      system: this.system(request),
      messages,
      tools: (request.toolDefinitions ?? request.tools).map((tool) => ({
        name: tool.name,
        description: tool.description,
        parametersSchema: tool.parametersSchema
      }))
    })
    const hasAttachments = [
      ...request.history.flatMap((turn) => turn.messages),
      request.input?.message
    ].some((message) => message?.attachments?.length)
    const hasOpaqueReasoning = messages.some(
      (message) =>
        Array.isArray(message.content) &&
        message.content.some((part) =>
          Object.values(part.providerOptions ?? {}).some(
            (options) =>
              options.signature ||
              options.redactedData ||
              options.reasoningEncryptedContent ||
              options.thoughtSignature
          )
        )
    )
    // 签名和密文的字节长度不是模型实际读取的 Token 数，不能据此误报超出预算。
    const hasMessageReasoning = messages.some(
      (message) => message.providerOptions?.openaiCompatible?.reasoning_details?.length
    )
    const estimatedInputTokens =
      hasAttachments || hasOpaqueReasoning || hasMessageReasoning
        ? null
        : Math.ceil(Buffer.byteLength(material, 'utf8') / 3)
    const modelLimit = request.connection.capabilities.contextTokens ?? null
    const { budget, reservedOutputTokens, inputBudget, thresholdTokens } = contextBudget(request)
    let fit = 'unknown'
    if (estimatedInputTokens !== null)
      fit = estimatedInputTokens <= inputBudget ? 'fits' : 'over-budget'
    if (inputBudget < 0) fit = 'over-budget'
    return {
      estimatedInputTokens,
      reservedOutputTokens,
      effectiveBudget: budget,
      thresholdTokens,
      modelLimit,
      limitSource: request.connection.capabilities.contextSource ?? null,
      fit,
      compression: {
        enabled: request.options.compression.enabled,
        abstractMessageId: null,
        retainedTurns: request.options.compression.retainedTurns
      },
      lastUsage: null,
      attachmentDecisions: await this.attachments.diagnostics(request),
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
    const system = this.templates.render('summary.njk', {})
    const prompt = JSON.stringify(
      semanticHistory(request.history).map((turn) => ({
        ...turn,
        messages: turn.messages.map((message) =>
          message.kind === 'assistant'
            ? { ...message, parts: message.parts.filter((part) => part.kind !== 'reasoning') }
            : message
        )
      }))
    )
    // 摘要请求也有自己的窗口，已知超限直接交给普通历史裁剪兜底。
    const estimate = Math.ceil(Buffer.byteLength(JSON.stringify({ system, prompt }), 'utf8') / 3)
    requireValue(
      estimate <= contextBudget(request).inputBudget,
      'context_over_budget',
      '待摘要材料超出模型输入预算。'
    )
    const response = await this.generate({
      ...this.parameters(request, signal),
      system,
      prompt
    })
    requireValue(response.text?.trim(), 'empty_response')
    return {
      text: response.text.trim(),
      usage: {
        inputTokens: response.usage?.inputTokens ?? null,
        outputTokens: response.usage?.outputTokens ?? null,
        reasoningTokens:
          response.usage?.outputTokenDetails?.reasoningTokens ??
          response.usage?.reasoningTokens ??
          null
      }
    }
  }
}
