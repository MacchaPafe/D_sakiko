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
 * @property {string} id 允许发言的角色。
 * @property {string} formId 本次形态。
 * @property {string} displayName 名称快照。
 * @property {string} description 形态描述及语气材料。
 * @property {Pick<import('../../shared/contracts/presentation.js').PerformanceCatalog, 'motions' | 'expressions'>} performances 本次普通演出目录，不包含目标状态能力或状态专用动作。
 */

/** @typedef {Pick<import('../../shared/contracts/conversation.js').CharacterLine, 'id' | 'speakerId' | 'formId' | 'text' | 'translation' | 'emotion' | 'performance'>} ContextLine 历史形态可能因导入缺失而未解析，不伪造形态。 */

/**
 * 临时语义投影保留 assistant 分组、reasoning 类别和 callId；不以扁平展示反推历史。
 * 缺图在所属用户内容中追加不可读取说明并保留问题，不伪造图像；附件可用性涵盖本次和历史材料。
 * 摘要投影仅取最后有效 abstract 及其后文，原存档不裁掉。
 * @typedef {{ messageId: string } & ({ kind: 'user', source: import('../../shared/contracts/conversation.js').UserMessage['source'], text: string,
 *   attachments: import('../../shared/contracts/conversation.js').Attachment[], unavailableAttachments: string[] }
 *   | { kind: 'assistant', parts: Array<import('../../shared/contracts/conversation.js').ReasoningPart
 *       | import('../../shared/contracts/conversation.js').ToolCallPart | { kind: 'dialogue', lines: ContextLine[] }>, protocol: import('../../shared/contracts/common.js').Metadata }
 *   | { kind: 'tool-result', callId: string, toolName: string, outcome: import('../../shared/contracts/conversation.js').ToolOutcome }
 *   | { kind: 'abstract', text: string })} ContextMessage
 */

/** @typedef {Extract<ContextMessage, { kind: 'user' }>} ContextUserMessage 仅输入消息投影。 */

/**
 * 保留 Turn 边界，预算裁剪不能拆散响应和对应结果；不传原 Chat 或可写消息对象。
 * @typedef {object} ContextTurn
 * @property {string} turnId 来源 Turn 身份。
 * @property {ContextMessage[]} messages 已应用最后有效摘要规则的有序投影。
 */

/** @typedef {Required<Omit<import('../../shared/contracts/settings.js').GenerationOptions, 'connectionId' | 'modelId'>>} ResolvedGenerationOptions 已解析的生成值，连接与模型仅由 connection 指定。 */

/**
 * @typedef {object} GeneratedLine
 * @property {string} id 本次输出稳定句身份，重试不能为已交付句重新生成 ID。
 * @property {string} speakerId 本轮角色。
 * @property {string} formId 本轮形态。
 * @property {string} text 完整合法正文。
 * @property {string | null} translation 翻译。
 * @property {string | null} emotion 合法情绪。
 * @property {import('../../shared/contracts/presentation.js').PerformanceSelection} performance 本轮目录中的演出选择。
 */

/**
 * 同一次非流式模型响应的有序增量：先接受实际 reasoning/调用，台词完成校验后再追加到同一容器。
 * appendParts 只追加此前未接受的稳定身份；一份响应仅有一个 responseId，不重复交付最终台词。
 * 修复请求是新响应；不能把修复输出伪装成原响应或把失败原文当台词。不是 token 流接口。
 * @typedef {object} ResponseUpdate
 * @property {string} responseId 稳定响应身份，作为 AssistantMessage.id。
 * @property {Array<import('../../shared/contracts/conversation.js').ReasoningPart
 *   | import('../../shared/contracts/conversation.js').ToolCallPart | { id: string, kind: 'dialogue', lines: GeneratedLine[] }>} appendParts 待接受的内容。
 * @property {import('../../shared/contracts/common.js').Metadata} protocol 必要协议材料，限脱敏白名单。
 */

/**
 * @typedef {object} GenerationRequest
 * @property {string} turnId 本次已受理 UUID，用于工具调用去重和来源关联，不授权读取 Chat。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 编排方式。
 * @property {AgentCharacter[]} characters 一次固定的角色材料。
 * @property {{ displayName: string, description: string } | null} userPersona 从 Chat 固定人格提取，不包含头像/来源/能力。
 * @property {ContextTurn[]} history 当前输入之前的轮次投影，保留完整裁剪单位。
 * @property {{ kind: 'message', message: ContextUserMessage } | { kind: 'scenario' }} input 手动/内部消息投影或主动情景命令。
 * @property {import('../../shared/contracts/settings.js').ScenarioSettings | null} scenario 固定情境，拼入 User Prompt，不伪造存档用户输入。
 * @property {ResolvedGenerationOptions} options 已完整解析的生成设置，不接收模板文本或路径，禁止下层再读全局覆盖。
 * @property {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 后端临时连接快照，与附件准备使用同一作用域。
 * @property {string[]} supplementalContext 允许直接供模型使用的内容，不含检索诊断。
 * @property {import('../../shared/contracts/tools.js').AgentTool[]} tools 已解析依赖和权限且固定的工具，不隐式注入未选择工具。
 */

/**
 * 依次 await 接受，再继续执行；回调不是可丢弃的 UI 通知。
 * 保存故障后 Conversation 可接受到内存并暂停新受理；拒绝接受时 Agent 停止推进，不重放工具。
 * 世界书诊断由 Conversation 绑定的查询函数单独接收，不增加 Agent 专用诊断通知，也不混入工具结果。
 * @typedef {object} GenerationObserver
 * @property {(update: ResponseUpdate) => Promise<void>} onResponse 接受原分组中的 reasoning、调用及合法台词，包含最终台词。
 * @property {(result: import('../../shared/contracts/conversation.js').ToolResultMessage) => Promise<void>} onToolResult 每个调用唯一结果，所有工具统一通知。
 */

/**
 * @typedef {object} TokenUsage
 * @property {number | null} inputTokens 实际输入消耗，供应商未给时 null。
 * @property {number | null} outputTokens 实际输出消耗。
 * @property {number | null} reasoningTokens 实际推理消耗，不能用估算冒充。
 */

/**
 * @typedef {object} ContextInspection
 * @property {number | null} estimatedInputTokens 包含 system、人格、角色、历史、输入、工具、输出 Schema、附件的估值；无法估计为 null。
 * @property {number} reservedOutputTokens 为生成预留的预算。
 * @property {number | null} modelLimit 已知模型上限，未知为 null。
 * @property {string | null} limitSource 目录、用户覆盖或供应商声明来源。
 * @property {'fits' | 'over-budget' | 'unknown'} fit 是否容纳请求。
 * @property {{ enabled: boolean, abstractMessageId: string | null, retainedTurns: number }} compression 当前有效摘要及压缩设置。
 * @property {TokenUsage | null} lastUsage Conversation 可补充最近实际请求消耗，Agent 预览无已知值时为 null，不冒充本次测量。
 * @property {import('../../shared/contracts/common.js').Problem[]} problems 未知能力、资源缺失或预算问题。
 */

/**
 * 压缩输入由 Conversation 在同一操作内固定，不把可改 Chat 传入 Agent。
 * @typedef {object} CompressionRequest
 * @property {ContextTurn[]} history 旧累计摘要和新增的完整可压缩轮次投影，不包含必须保留的末轮。
 * @property {ResolvedGenerationOptions} options 固定预算及生成参数；摘要模板由 Agent 内部提供，连接与模型由 connection 指定。
 * @property {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 本次连接快照。
 */

/** @typedef {Omit<GenerationRequest, 'turnId' | 'input'> & { input: GenerationRequest['input'] | null }} ContextInspectionRequest 只读检查无需创建 Turn，null 输入只检查历史和配置。 */

/**
 * 超预算先由 Conversation 协调自动压缩；失败保留原文/旧摘要。仍需裁剪时只裁完整旧 Turn，
 * 不拆开 assistant 与对应结果，不删本次输入、必需提示或保留末轮；必需材料仍超限则明确失败。
 * Agent 管提示、单轮预算、模型和工具循环；LLM 适配负责供应商参数与协议转换。
 * 内置模板只渲染本次固定材料，渲染前校验输入；执行与上下文预览共用渲染规则。
 * 同响应工具按调用顺序串行：sync 等后端完成，async 等可靠 accepted 后继续，不等待最终交互结果。
 * sync 内部可使用 Promise；需要前端交互的工具必须使用 async，不能挂起当前 Loop 等待用户回答。
 * 重试只重试失败请求，不重放已执行工具或重复通知；同调用去重，显式新 Turn 是新执行。
 * 校验 Schema、角色/形态/语言/情绪/演出目录，格式修复有界；失败原文只留诊断，不进入正式上下文或 Speech。
 * 失败稳定类别至少含 timeout/authentication/rate_limit/quota_exhausted/empty_response/invalid_output/unsupported_input。
 * 仅瞬时错误按上限和退避重试；鉴权、额度及无效配置直接失败，保留脱敏 Problem，原始异常只入诊断。
 * 附件能力同时检查历史与本次材料，区分输入能力和上传能力。未知不能推定支持。
 * 文件引用失效只使实际旧引用失效；同连接快照重新准备，再判断重试安全性，不重跑整个 Loop。
 * 停止/失败后缺结果调用交由 Conversation 结算；已 accepted 的交互不产生第二个协议结果。
 * 已接入 AI SDK；外部供应商模型请求与工具循环均由本接口实现。
 * @see ../../../docs/nunjucks-prompts.md 模板归属、Nunjucks 选型及渲染约定。
 */
export class Agent {
  /**
   * 绑定模板、私有凭据和模型传输；不读取当前页面或全局对话状态。
   * @param {object} options 宿主依赖。
   * @param {(scope: string) => string} options.credential 解析本次固定凭据的后端能力。
   * @param {typeof generateText} [options.generate] AI SDK 请求入口，测试可替换。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 内联附件读取能力。
   * @param {string} [options.templateDirectory] 可信内置模板目录，禁止模型或页面提交模板。
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
   * 按本次连接快照创建供应商模型适配器。
   * @param {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 已解析连接，不自行重新读取设置。
   * @returns {import('ai').LanguageModel} 供实际请求与连接探测共用的模型。
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
  /** 角色描述仅作为模板数据插值，不进行第二次渲染，避免把角色文本当成模板代码。 */
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
  /** 执行与预算预览共用历史投影；预览可使用附件占位，不触发上传或模型调用。 */
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
  /** 根据已声明的附件能力投影用户内容，未知能力不推定为可发送。 */
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
      const data = inspect ? new Uint8Array(0) : await this.assets.read(attachment.asset)
      content.push(
        image
          ? { type: 'image', image: data, mediaType: info.mediaType }
          : { type: 'file', data, mediaType: info.mediaType, filename: info.name }
      )
    }
    return content
  }
  /** 将固定生成参数映射到 SDK；重试限定于模型请求，不包括已执行的工具。 */
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
  /** 格式合法后仍校验角色与演出目录，防止模型输出越过本轮提供的能力范围。 */
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
   * 根据固定输入执行一轮模型与工具循环，交付角色台词、推理记录和工具调用结果。
   * 所有内容都经 observer 接受，返回值不再重复最终台词。取消不撤销已接受过程及外部副作用。
   * @param {GenerationRequest} request 固定输入。
   * @param {GenerationObserver} observer 顺序接收者。
   * @param {AbortSignal} signal 取消信号。
   * @returns {Promise<{ status: 'completed' | 'cancelled', usage: TokenUsage | null }>} Loop 结束，不代表 Turn 已 finished；不可恢复错误拒绝。
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
      // 调用记录先由对话模块接受，再执行副作用；取消或中断时才有可追溯的调用身份。
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
          // 工具结果已经写入 messages；仅修复台词格式，不从轮次开头重跑工具。
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
   * 预览本次请求的上下文占用、预算是否足够及模型输入能力问题。
   * 与 run 共用请求准备规则，估计附件开销但不上传、不调用模型/工具、不压缩或写存档。
   * @param {ContextInspectionRequest} request 要检查的材料，不创建运行身份。
   * @returns {Promise<ContextInspection>} 占用和能力诊断。
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
   * 将选定的完整历史轮次和旧摘要压缩为新的累计摘要文本。
   * 自动压缩也由 Conversation 在当前生成操作内调用并确定插入位置；失败保留原文和旧摘要。
   * @param {CompressionRequest} request 可压缩的完整材料。
   * @param {AbortSignal} signal 取消。
   * @returns {Promise<{ text: string, usage: TokenUsage | null }>} 累计摘要文本，不生成角色台词。
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
