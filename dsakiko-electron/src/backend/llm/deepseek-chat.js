import { officialDeepSeekChat, officialFlash, webConflict } from '../../shared/tool-options.js'
import { requireValue } from '../core/support.js'

/** @param {object} request 已解析的轮次请求。 @param {object} chat 对话。 @returns {void} 将用户选择映射为官方协议中的能力。 */
export function configureDeepSeek(request, chat) {
  if (!officialDeepSeekChat(request.connection)) return
  const flash = officialFlash(request.connection)
  const names = request.tools.map((tool) => tool.name)
  requireValue(
    !webConflict(request.connection, names, request.options.reasoning),
    'unsupported_tools',
    '同时使用 DeepSeek 联网和本地工具需要开启思考。'
  )
  const native = []
  if (flash && names.includes('web_search')) native.push('deepseek_web_search')
  if (flash && names.includes('read_url')) native.push('deepseek_read_url')
  let reminder = null
  if (names.includes('get_current_datetime')) {
    const date = new Date()
    const day = date.toLocaleDateString('sv-SE')
    reminder = [day, date.toLocaleDateString('zh-CN', { weekday: 'long' }), '', 'App', '中文'].join(
      ','
    )
    request.supplementalContext.push(
      '当前本地时间（计算相对提醒的基准）：' +
        date.toLocaleString('sv-SE') +
        '，时区 ' +
        Intl.DateTimeFormat().resolvedOptions().timeZone +
        '，UTC 偏移分钟 ' +
        -date.getTimezoneOffset()
    )
  }
  const substituted = new Set(
    flash ? ['get_current_datetime', 'web_search', 'read_url'] : ['get_current_datetime']
  )
  request.tools = request.tools.filter((tool) => !substituted.has(tool.name))
  const allowed = new Set(request.tools.map((tool) => tool.name))
  request.toolDefinitions = (request.toolDefinitions ?? request.tools)
    .filter((tool) => (!native.length || allowed.has(tool.name)) && !substituted.has(tool.name))
    .map(({ name, description, parametersSchema }) => ({ name, description, parametersSchema }))
    .map((tool) =>
      tool.name === 'create_reminder'
        ? { ...tool, description: '使用本轮提供的本地时间和时区，创建到期后主动开口的提醒。' }
        : tool
    )
  request.deepseek = {
    native,
    reminder,
    title:
      flash &&
      chat.mode === 'single-character' &&
      !chat.turns.length &&
      !chat.meta.extensions?.manualTitle &&
      !chat.meta.extensions?.generatedTitle &&
      request.input?.message?.source?.kind === 'manual'
  }
}

/** SDK 负责图片、推理和函数消息；仅在传输边界还原 SDK 尚未建模的服务端字段。 */
export function nativeRequest(messages, plan, transport) {
  const metadata = new Map()
  const prompt = messages.map((message, index) => {
    const extra = message.nativeMetadata
    if (!extra) return message
    const name = 'dsakiko_' + index
    metadata.set(name, extra)
    const rest = { ...message }
    delete rest.nativeMetadata
    return { ...rest, providerOptions: { ...rest.providerOptions, deepseek: { name } } }
  })
  const wire = async (url, init) => {
    const body = JSON.parse(init.body)
    body.messages = body.messages.flatMap((message) => {
      const extra = metadata.get(message.name)
      if (!extra) return [message]
      delete message.name
      if (extra.server_tool_calls) message.server_tool_calls = extra.server_tool_calls
      if (extra.reasoning_content !== undefined) message.reasoning_content = extra.reasoning_content
      return extra.reminder
        ? [{ role: 'latest_reminder', content: extra.reminder }, message]
        : [message]
    })
    if (plan.native.length)
      body.tools = [...(body.tools ?? []), ...plan.native.map((type) => ({ type }))]
    if (plan.title) body.title = { enabled: true }
    return transport(url, { ...init, body: JSON.stringify(body) })
  }
  return { messages: prompt, transport: wire }
}
