import { EventSourceParserStream } from 'eventsource-parser/stream'
import { openai } from '@ai-sdk/openai'
import { requireValue, failure } from '../core/support.js'
import { resource } from '../settings/chatgpt-oauth.js'

/**
 * 按本轮已授权的工具选择替换搜索后端，避免工具目录里的博查声明同时进入请求。
 * @param {import('./interface.js').GenerationRequest} request 本轮请求。
 * @returns {void}
 */
export function configureChatGPTSearch(request) {
  if (request.connection.provider !== 'chatgpt') return
  request.chatgptWebSearch = request.tools.some((tool) => tool.name === 'web_search')
  if (request.toolDefinitions)
    request.toolDefinitions = request.toolDefinitions.filter((tool) => tool.name !== 'web_search')
  request.tools = request.tools.filter((tool) => tool.name !== 'web_search')
}

export const chatGPTWebSearchTool = openai.tools.webSearch()

/**
 * 提取已执行的搜索与引用供对话展示，SDK 自己负责把成功的搜索结果编码回 Responses。
 * @param {object} response SDK 单步生成结果。
 * @returns {import('../../shared/server-web.js').ServerWebActivity} 网页活动记录。
 */
export function chatGPTWebActivity(response) {
  const calls = []
  const citations = []
  for (const part of response.content ?? []) {
    if (part.type === 'text')
      for (const { source } of part.citations ?? []) {
        if (source.sourceType === 'url') citations.push({ url: source.url, title: source.title })
      }
    if (!part.providerExecuted || part.toolName !== 'web_search') continue
    if (part.type === 'tool-result') {
      const { action, sources } = part.output ?? {}
      calls.push({
        id: part.toolCallId,
        toolName: 'web_search',
        arguments: action ?? {},
        outcome: { status: 'succeeded' },
        sources:
          sources?.filter((item) => item.type === 'url') ??
          (action?.url ? [{ url: action.url }] : [])
      })
    }
    if (part.type === 'tool-error') {
      calls.push({
        id: part.toolCallId,
        toolName: 'web_search',
        arguments: {},
        outcome: {
          status: 'failed',
          problem: {
            code: 'server_web_failed',
            message:
              part.error?.status === 'incomplete'
                ? 'OpenAI 网页搜索未完成。'
                : 'OpenAI 网页搜索失败。',
            retryable: false
          }
        },
        sources: []
      })
    }
  }
  return { calls, citations }
}

/** 订阅端点采用无服务端存储的流式请求，本地函数需要声明命名空间。 */
function subscriptionBody(options) {
  const body = JSON.parse(options.body)
  for (const field of ['temperature', 'top_p', 'max_output_tokens']) delete body[field]
  body.store = false
  body.stream = true
  for (const item of body.input ?? []) {
    if (item.role === 'system') item.role = 'developer'
    if (item.type === 'function_call') item.namespace = 'dsakiko'
  }
  if (body.tools?.length) {
    requireValue(
      body.tools.every((tool) => ['function', 'web_search'].includes(tool.type)),
      'unsupported_tool',
      '当前 ChatGPT 订阅连接支持本地函数和原生网页搜索。'
    )
    const functions = body.tools.filter((tool) => tool.type === 'function')
    body.tools = body.tools.filter((tool) => tool.type === 'web_search')
    if (functions.length)
      body.tools.unshift({
        type: 'namespace',
        name: 'dsakiko',
        description: '数字小祥的本地工具',
        tools: functions
      })
    if (body.tool_choice?.type === 'function') body.tool_choice.namespace = 'dsakiko'
  }
  return body
}

/** 订阅用量和搜索权限的错误在 HTTP 和流结束事件中采用同样的提示。 */
function subscriptionFailure(error, tools, status) {
  const code = error?.code
  if (code === 'subscription_sharing_usage_limit_exceeded')
    return failure('quota_exhausted', '此 ChatGPT 注册的可用额度已耗尽，请查看订阅用量或切换连接。')
  if (code === 'subscription_sharing_usage_unavailable')
    return failure('quota_unavailable', '此 ChatGPT 注册暂时无法使用订阅额度。')
  if (code === 'subscription_sharing_user_not_eligible')
    return failure('quota_unavailable', '当前 ChatGPT 账号或工作区无法使用订阅共享。')

  if (!tools?.some((tool) => tool.type === 'web_search')) return null
  const param = /^tools(?:\[(\d+)\]|\.(\d+))/.exec(error?.param ?? '')
  const index = param?.[1] ?? param?.[2]
  const message = typeof error?.message === 'string' ? error.message : ''
  const searchError =
    (index !== undefined && tools[index]?.type === 'web_search') ||
    /\bweb(?:_|[ -])search\b/i.test(message)
  if (!searchError) return null

  if (status === 403 || /permission|not allowed|disabled|not enabled/i.test(message))
    return failure('web_search_forbidden', '当前 ChatGPT 账号或工作区没有原生网页搜索权限。')
  if (
    /unsupported|not supported/i.test(message) ||
    ['unsupported_tool', 'unsupported_value'].includes(code)
  )
    return failure('web_search_unsupported', '当前 ChatGPT 订阅模型不支持原生网页搜索。')
  return failure('web_search_rejected', 'ChatGPT 拒绝了原生网页搜索请求，请检查模型与搜索权限。')
}

/** 将已完整结束的流交回现有 Responses 解码器，断流不能生成部分存档。
 * @param {(rejected?: string) => Promise<string>} access 读取或更新当前注册的令牌。
 * @param {typeof fetch} transport 可注入的网络传输。
 * @returns {typeof fetch} 仅限订阅官方推理端点的传输。
 */
export function subscriptionFetch(access, transport) {
  return async (url, options) => {
    requireValue(
      String(url) === resource + '/responses',
      'invalid_connection',
      '订阅只允许访问 OpenAI 官方推理地址。'
    )
    const request = subscriptionBody(options)
    const body = JSON.stringify(request)
    async function send(token) {
      const headers = new Headers(options.headers)
      headers.set('Authorization', 'Bearer ' + token)
      headers.set('Accept', 'text/event-stream')
      headers.set('User-Agent', 'D_sakiko/0.1 Electron')
      return transport(url, { ...options, body, headers, redirect: 'error' })
    }
    const token = await access()
    let response = await send(token)
    if (response.status === 401) {
      const error = await response
        .clone()
        .json()
        .catch(() => null)
      if (['invalid_token', 'expired_token', 'token_expired'].includes(error?.error?.code))
        response = await send(await access(token))
    }
    if (!response.ok) {
      const data = await response
        .clone()
        .json()
        .catch(() => null)
      const rejected = subscriptionFailure(data?.error, request.tools, response.status)
      if (rejected) throw rejected
      return response
    }
    requireValue(response.body, 'incomplete_response', 'ChatGPT 返回空响应。')
    const events = response.body
      .pipeThrough(new TextDecoderStream())
      .pipeThrough(new EventSourceParserStream())
    const outputItems = new Map()
    for await (const event of events) {
      if (event.data === '[DONE]') break
      const data = JSON.parse(event.data)
      if (data.type === 'response.output_item.done' && Number.isInteger(data.output_index))
        outputItems.set(data.output_index, data.item)
      if (data.type === 'response.completed' && data.response?.status === 'completed') {
        // 订阅流可在结束事件中省略 output，只有确认完成后才能交付此前收齐的项。
        const output = data.response.output?.length
          ? data.response.output
          : [...outputItems.keys()]
              .sort((left, right) => left - right)
              .map((index) => outputItems.get(index))
        return Response.json({ ...data.response, output })
      }
      if (['response.failed', 'response.incomplete', 'error'].includes(data.type)) {
        const rejected = subscriptionFailure(
          data.response?.error ?? data.error ?? data,
          request.tools
        )
        if (rejected) throw rejected
        throw failure('model_request_failed', 'ChatGPT 生成未完成，请检查账号用量和模型权限。')
      }
    }
    throw failure('incomplete_response', 'ChatGPT 响应中断，请重试。')
  }
}
