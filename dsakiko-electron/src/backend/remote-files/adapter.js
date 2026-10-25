import { uploadFile } from 'ai'
import { createProvider } from '../llm/adapters.js'
import { normalizeConnection } from '../llm/connections.js'
import { fingerprint, requireValue, failure } from '../core/support.js'

/**
 * @typedef {object} FileAdapter
 * @property {string} scopeId 不包含明文密钥的服务作用域摘要。
 * @property {string} provider 连接的厂商身份。
 * @property {string} protocol 文件引用对应的生成协议。
 * @property {boolean} canUpload SDK 上传实现是否可用。
 * @property {(info: object, data: Uint8Array, signal: AbortSignal) => Promise<object>} upload 返回引用、删除标识和有效期。
 * @property {(record: object) => Promise<void>} [delete] 可选删除能力，确认后才完成。
 */

/** 仅认可明确的上传能力错误，普通 HTTP 错误不能用于自动降级。
 * @param {unknown} error 上传异常。
 * @returns {boolean} 是否允许尝试内联。
 */
export function uploadUnsupported(error) {
  return (
    error?.name === 'AI_UnsupportedFunctionalityError' &&
    /file upload|uploading files/i.test(error.functionality ?? '')
  )
}

/** 绑定协议、地址与凭据；仅摘要进入持久索引，密钥留在闭包。
 * @param {object} connection 固定连接快照。
 * @param {string} apiKey 本次凭据。
 * @param {typeof fetch} [transport] 可替换传输。
 * @returns {FileAdapter} 上传适配及可选的删除能力。
 */
export function createFileAdapter(connection, apiKey, transport = fetch) {
  const value = normalizeConnection(connection)
  const provider = createProvider(value, apiKey, { enabled: false }, transport)
  const api = typeof provider.files === 'function' ? provider.files() : null
  let namespace = 'openai'
  if (value.protocol === 'anthropic-messages') namespace = 'anthropic'
  else if (value.protocol === 'google-generative-ai') namespace = 'google'
  else if (value.provider === 'deepseek') namespace = 'deepseek'
  const purpose = namespace === 'google' || namespace === 'anthropic' ? 'message' : 'user_data'
  const scopeId = fingerprint({
    provider: value.provider,
    baseUrl: value.baseUrl,
    protocol: value.protocol,
    apiKey,
    purpose
  })
  const adapter = {
    scopeId,
    provider: value.provider,
    protocol: value.protocol,
    canUpload: typeof api?.uploadFile === 'function',
    async upload(info, data, signal) {
      requireValue(adapter.canUpload, 'file_upload_unavailable')
      const options = {}
      if (namespace === 'openai') options.openai = { purpose: 'user_data' }
      if (namespace === 'google') options.google = { displayName: info.name }
      const result = await uploadFile({
        api,
        data,
        mediaType: info.mediaType,
        ...(namespace === 'google' ? {} : { filename: info.name }),
        providerOptions: options,
        abortSignal: signal
      })
      const reference = result.providerReference
      requireValue(
        reference && typeof reference[namespace] === 'string' && reference[namespace],
        'invalid_remote_file',
        '上传没有返回可用文件引用。'
      )
      const metadata = result.providerMetadata?.[namespace] ?? {}
      let expiry = result.expiresAt?.getTime() ?? null
      if (expiry === null && typeof metadata.expiresAt === 'number')
        expiry = metadata.expiresAt * 1000
      if (expiry === null && metadata.expirationTime) expiry = Date.parse(metadata.expirationTime)
      return {
        reference,
        deleteId: namespace === 'google' ? (metadata.name ?? null) : reference[namespace],
        expiresAt: Number.isFinite(expiry) ? expiry : null,
        warnings: result.warnings.map(({ type, feature }) => ({ type, feature: feature ?? null }))
      }
    }
  }
  // 只补齐已核对的 DeepSeek 原生协议；其他 SDK 的可选删除能力留待明确接入。
  if (value.provider === 'deepseek' && value.protocol === 'chat-completions') {
    adapter.delete = async (record) => {
      requireValue(typeof record.deleteId === 'string' && record.deleteId, 'invalid_remote_file')
      const response = await transport(
        `${value.baseUrl}/files/${encodeURIComponent(record.deleteId)}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${apiKey}` },
          signal: AbortSignal.timeout(15000)
        }
      )
      // 普通 404 也可能是错误路由；必须有明确的文件不存在代码才确认删除。
      const body = await response.json().catch(() => null)
      if (response.status === 404 && body?.error?.code === 'file_not_found') return
      if (!response.ok || body?.deleted !== true || body.id !== record.deleteId)
        throw failure('remote_delete_failed', '远端文件删除未得到确认。', true)
    }
  }
  return adapter
}

/** 识别明确的 DeepSeek 引用失效；不使用鉴权、限流错误触发失效。
 * @param {unknown} error 原始 SDK 异常。
 * @returns {null | {ids: string[]}} 可供当前请求引用匹配的拒绝信息。
 */
export function rejectedRemoteFiles(error) {
  const cause = error?.lastError ?? error
  if (cause?.statusCode !== 400) return null
  const code = cause.data?.error?.code
  const message = String(cause.data?.error?.message ?? cause.message ?? '')
  if (
    !['invalid_file_id', 'file_not_found', 'file_expired'].includes(code) &&
    !/invalid file_id|file_ids do not exist|file.+not created under your account/i.test(message)
  )
    return null
  return { ids: [...new Set(message.match(/file-(?:api-)?[a-zA-Z0-9_-]+/g) ?? [])] }
}
