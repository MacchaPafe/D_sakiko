import { randomUUID } from 'node:crypto'
import { problemError } from '../../shared/contracts/desktop.js'

function parseReply(content, characters) {
  const cleaned = content
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '')
  const value = JSON.parse(cleaned)
  if (value.type === 'tool') {
    if (
      value.name !== 'get_current_time' ||
      !value.arguments ||
      typeof value.arguments !== 'object' ||
      Array.isArray(value.arguments) ||
      Object.keys(value.arguments).length
    )
      throw new Error('工具仅允许 get_current_time，arguments 必须为 {}。')
    return value
  }
  if (
    value.type !== 'final' ||
    !Array.isArray(value.lines) ||
    !value.lines.length ||
    value.lines.length > 12
  )
    throw new Error('最终结果必须是 type=final，lines 必须含 1–12 句台词。')
  value.lines = value.lines.map((line) => {
    const speaker = characters.find((item) => item.id === line.speakerId)
    if (!speaker || typeof line.text !== 'string' || !line.text.trim() || line.text.length > 1000)
      throw new Error('每句需要有效的 speakerId 和长度不超过 1000 的非空 text。')
    if (line.translation !== null && typeof line.translation !== 'string')
      throw new Error('translation 必须为文本或 null。')
    if (line.emotion !== null && typeof line.emotion !== 'string')
      throw new Error('emotion 必须为文本或 null。')
    const selection = line.performance
    if (!selection || !['motion', 'expression'].every((key) => typeof selection[key] === 'string'))
      throw new Error('performance 必须包含 motion、expression。')
    if (
      selection.motion !== 'auto' &&
      !speaker.performances.motions.some((item) => item.id === selection.motion)
    )
      throw new Error('动作必须使用提供的编号或 auto。')
    if (
      selection.expression !== 'auto' &&
      !speaker.performances.expressions.some((item) => item.id === selection.expression)
    )
      throw new Error('表情必须使用提供的编号或 auto。')
    return {
      kind: 'character',
      speakerId: line.speakerId,
      text: line.text.trim(),
      translation: line.translation,
      emotion: line.emotion,
      performance: selection
    }
  })
  return value
}

function systemPrompt(request, language) {
  const description = request.characters.map((character) => ({
    ...character,
    performances: {
      motions: character.performances.motions.slice(0, 100),
      expressions: character.performances.expressions.slice(0, 100)
    }
  }))
  return `你正在参与角色对话。严格遵循角色设定，不替用户发言。${request.mode === 'scripted-dialogue' ? '为两名角色编写有顺序的自然对话，每轮两名角色都应参与。' : '为指定角色回复用户。'}
用户人格：${JSON.stringify(request.userPersona)}
角色及可选演出：${JSON.stringify(description)}
台词语言：${language === 'ja' ? '日语；translation 提供中文翻译' : '中文；translation 为 null'}。
你每次只返回一个 JSON 对象，不使用 Markdown。需要当前日期或时间时，必须先调用时间工具，不能猜测。
工具请求：{"type":"tool","name":"get_current_time","arguments":{}}
工具结果会作为下一条输入提供，然后你可以继续请求工具或完成回复。
最终回复：{"type":"final","lines":[{"speakerId":"角色编号","text":"完整的一句话","translation":null,"emotion":"happiness","performance":{"motion":"auto","expression":"auto"}}]}
每轮 1–12 句，通常 2–6 句。speakerId 只能来自角色列表，动作和表情只能选择该角色的目录编号或 auto。最终台词必须使用完整句子，不能包含工具调用说明。`
}

/** 有限的 JSON 工具循环，格式修复不会重放已经执行的工具。 */
export class Agent {
  constructor(getConnection, fetcher = fetch) {
    this.getConnection = getConnection
    this.fetcher = fetcher
  }
  async run(request, observer, signal) {
    const connection = this.getConnection()
    if (!connection.baseUrl || !connection.model)
      throw problemError('missing_connection', '请在设置中填写模型地址与模型名称。')
    const messages = [{ role: 'system', content: systemPrompt(request, connection.language) }]
    const history = []
    let available = request.model.contextTokenBudget * 2 - messages[0].content.length - 3000
    for (const message of [...request.history].reverse()) {
      if (message.kind === 'reasoning') continue
      const content = message.speakerId ? `${message.speakerId}: ${message.text}` : message.text
      if (content.length > available) break
      available -= content.length
      history.unshift({ role: message.kind === 'character' ? 'assistant' : 'user', content })
    }
    messages.push(...history, { role: 'user', content: request.input.message.text })
    if (
      messages[0].content.length + request.input.message.text.length >
      request.model.contextTokenBudget * 3
    )
      throw problemError('context_too_large', '角色描述或输入超过当前上下文预算，请缩短后重试。')
    const endpoint =
      connection.baseUrl.replace(/\/$/, '').replace(/\/chat\/completions$/, '') +
      '/chat/completions'
    let repairs = 0
    for (let step = 0; step < 8; step += 1) {
      if (signal.aborted) return { status: 'cancelled' }
      const timeout = new AbortController()
      const cancel = () => timeout.abort()
      signal.addEventListener('abort', cancel, { once: true })
      const timer = setTimeout(() => timeout.abort(), connection.requestTimeoutMs)
      let data
      try {
        const response = await this.fetcher(endpoint, {
          method: 'POST',
          signal: timeout.signal,
          headers: {
            'Content-Type': 'application/json',
            ...(connection.apiKey ? { Authorization: `Bearer ${connection.apiKey}` } : {})
          },
          body: JSON.stringify({
            model: request.model.model,
            temperature: request.model.temperature,
            stream: false,
            messages
          })
        })
        if (!response.ok)
          throw problemError(
            'model_request_failed',
            `模型请求失败（HTTP ${response.status}），请检查地址、模型和凭据。`,
            true
          )
        data = await response.json()
      } catch (error) {
        if (signal.aborted) return { status: 'cancelled' }
        if (timeout.signal.aborted)
          throw problemError('model_timeout', '模型请求超时，可调整设置后重试。', true)
        if (error.problem) throw error
        throw problemError('model_unavailable', '无法取得模型响应，请检查网络和模型设置。', true)
      } finally {
        clearTimeout(timer)
        signal.removeEventListener('abort', cancel)
      }
      if (signal.aborted) return { status: 'cancelled' }
      const message = data.choices?.[0]?.message
      if (typeof message?.reasoning_content === 'string' && message.reasoning_content.trim())
        await observer.onIntermediate([{ kind: 'reasoning', text: message.reasoning_content }])
      const content = message?.content
      if (typeof content !== 'string')
        throw problemError('invalid_model_response', '模型没有返回可处理的文本。', true)
      messages.push({ role: 'assistant', content })
      let reply
      try {
        reply = parseReply(content, request.characters)
      } catch (error) {
        if (repairs >= 2)
          throw problemError('invalid_model_format', '模型连续返回无效台词，格式纠正未成功。', true)
        repairs += 1
        messages.push({
          role: 'user',
          content: `格式校验失败：${error.message}。请纠正上一条结果并只返回合法 JSON；已提供的工具结果仍有效。`
        })
        continue
      }
      if (reply.type === 'final') return { status: 'completed', lines: reply.lines, summary: null }
      const record = {
        callId: randomUUID(),
        toolName: reply.name,
        arguments: {},
        status: 'running',
        result: null,
        error: null
      }
      await observer.onToolActivity(record)
      if (signal.aborted) return { status: 'cancelled' }
      const now = new Date()
      const result = {
        iso: now.toISOString(),
        local: now.toLocaleString('zh-CN', { hour12: false }),
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone
      }
      await observer.onToolActivity({ ...record, status: 'succeeded', result })
      messages.push({
        role: 'user',
        content: `get_current_time 工具结果：${JSON.stringify(result)}`
      })
    }
    throw problemError('agent_limit', '本轮已达到工具循环上限，请重新发送。', true)
  }
}
