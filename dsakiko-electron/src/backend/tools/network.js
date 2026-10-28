import { requireValue, failure } from '../core/support.js'

/**
 * 共享超时与有界读取，避免外部接口长时间挂起或返回无限结果。
 * @param {typeof fetch} transport 网络传输。
 * @param {string | URL} url 请求地址。
 * @param {RequestInit} options 请求参数。
 * @param {AbortSignal} [signal] 调用方取消信号。
 * @param {number} timeoutMs 请求上限毫秒。
 * @returns {Promise<unknown>} 解析后的有界 JSON 数据。
 */
export async function fetchJson(transport, url, options = {}, signal, timeoutMs = 12000) {
  const timeout = AbortSignal.timeout(timeoutMs)
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout
  try {
    const response = await transport(url, { ...options, signal: combined, redirect: 'error' })
    requireValue(response.ok, 'tool_http_error', `外部查询失败（HTTP ${response.status}）。`)
    const reader = response.body.getReader()
    const chunks = []
    let length = 0
    try {
      while (true) {
        combined.throwIfAborted()
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
  } catch (error) {
    signal?.throwIfAborted()
    if (timeout.aborted) throw failure('tool_timeout', '外部查询超时。', true)
    if (error.problem) throw error
    throw failure('tool_network_error', '外部查询失败，请检查网络或服务配置。', true)
  }
}
