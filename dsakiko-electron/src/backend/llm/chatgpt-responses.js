import { EventSourceParserStream } from 'eventsource-parser/stream'
import { requireValue, failure } from '../core/support.js'
import { resource } from '../settings/chatgpt-oauth.js'

/** 订阅端点只接受无服务端存储的流式请求，工具由本地 Agent 继续执行。 */
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
      body.tools.every((tool) => tool.type === 'function'),
      'unsupported_tool',
      'ChatGPT 订阅仅支持本地函数工具。'
    )
    body.tools = [
      { type: 'namespace', name: 'dsakiko', description: '数字小祥的本地工具', tools: body.tools }
    ]
    if (body.tool_choice?.type === 'function') body.tool_choice.namespace = 'dsakiko'
  }
  return JSON.stringify(body)
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
    const body = subscriptionBody(options)
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
    if (!response.ok) return response
    requireValue(response.body, 'incomplete_response', 'ChatGPT 返回空响应。')
    const events = response.body
      .pipeThrough(new TextDecoderStream())
      .pipeThrough(new EventSourceParserStream())
    for await (const event of events) {
      if (event.data === '[DONE]') break
      const data = JSON.parse(event.data)
      if (data.type === 'response.completed' && data.response?.status === 'completed')
        return Response.json(data.response)
      if (['response.failed', 'response.incomplete', 'error'].includes(data.type)) {
        const code = data.response?.error?.code ?? data.error?.code ?? data.code
        if (code === 'subscription_sharing_usage_limit_exceeded')
          throw failure(
            'quota_exhausted',
            '此 ChatGPT 注册的可用额度已耗尽，请查看订阅用量或切换连接。'
          )
        if (code === 'subscription_sharing_usage_unavailable')
          throw failure('quota_unavailable', '此 ChatGPT 注册暂时无法使用订阅额度。')
        throw failure('model_request_failed', 'ChatGPT 生成未完成，请检查账号用量和模型权限。')
      }
    }
    throw failure('incomplete_response', 'ChatGPT 响应中断，请重试。')
  }
}
