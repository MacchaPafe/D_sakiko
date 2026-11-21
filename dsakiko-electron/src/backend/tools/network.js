import { requireValue, failure } from '../core/support.js'

/** 在同一大小限制内读取成功和错误响应，避免信任远端声明的长度。 */
async function readBoundedJson(response, signal) {
  const reader = response.body.getReader()
  const chunks = []
  let length = 0
  try {
    while (true) {
      signal.throwIfAborted()
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      requireValue(length <= 2 * 1024 * 1024, 'tool_response_too_large')
      chunks.push(Buffer.from(value))
    }
  } finally {
    await reader.cancel().catch(() => {})
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

/**
 * 共享超时与有界读取，避免外部接口长时间挂起或返回无限结果。
 * @param {typeof fetch} transport 网络传输。
 * @param {string | URL} url 请求地址。
 * @param {RequestInit} options 请求参数。
 * @param {AbortSignal} [signal] 调用方取消信号。
 * @param {number} timeoutMs 请求上限毫秒。
 * @param {Partial<Record<number, (body: unknown) => string | undefined>>} [httpErrorMessages] 特定 HTTP 状态的本地化提示。
 * @returns {Promise<unknown>} 解析后的有界 JSON 数据。
 */
export async function fetchJson(
  transport,
  url,
  options = {},
  signal,
  timeoutMs = 12000,
  httpErrorMessages
) {
  const timeout = AbortSignal.timeout(timeoutMs)
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout
  try {
    const response = await transport(url, { ...options, signal: combined, redirect: 'error' })
    if (response.ok) return await readBoundedJson(response, combined)

    const resolveMessage = httpErrorMessages?.[response.status]
    let message
    if (resolveMessage) {
      // 代理也可能返回同一状态码，无法识别响应时保留通用提示。
      const body = await readBoundedJson(response, combined).catch(() => undefined)
      message = resolveMessage(body)
    }
    throw failure('tool_http_error', message ?? `外部查询失败（HTTP ${response.status}）。`)
  } catch (error) {
    signal?.throwIfAborted()
    if (timeout.aborted) throw failure('tool_timeout', '外部查询超时。', true)
    if (error.problem) throw error
    throw failure('tool_network_error', '外部查询失败，请检查网络或服务配置。', true)
  }
}
