import { copy, requireValue, failure } from '../core/support.js'
import { normalizeConnection } from '../llm/connections.js'
import { createFileAdapter, uploadUnsupported } from './adapter.js'
import { normalizeMediaType, readAttachmentRules, selectAttachmentTransport } from './policy.js'

/** 每次请求共享规则和连接快照，不把运行记录写回 Chat。 */
export class AttachmentPreparation {
  constructor({ assets, remoteFiles, credential, transport }) {
    this.assets = assets
    this.remoteFiles = remoteFiles
    this.credential = credential
    this.transport = transport
    this.requests = new WeakMap()
  }

  snapshot(request) {
    let pending = this.requests.get(request)
    if (!pending) {
      pending = (async () => {
        const connection = normalizeConnection(request.connection)
        const rules = await readAttachmentRules(this.remoteFiles?.rulesFile ?? null)
        const adapter = createFileAdapter(
          connection,
          this.credential(connection.credentialScope),
          this.transport
        )
        return { rules, connection, adapter, records: new Map(), decisions: new Map() }
      })()
      this.requests.set(request, pending)
    }
    return pending
  }

  /** 把受理阶段快照交给本次生成；再次使用原请求生成时重新读取规则。 */
  transfer(source, target) {
    const snapshot = this.requests.get(source)
    if (snapshot) this.requests.set(target, snapshot)
    this.requests.delete(source)
  }

  async content(message, request, inspect, signal) {
    const content = [{ type: 'text', text: message.text || '（附件）' }]
    if (!message.attachments?.length) return content
    const context = await this.snapshot(request)
    for (const attachment of message.attachments) {
      signal?.throwIfAborted()
      const metadata = await this.assets.inspect(attachment.asset)
      // 文档只通过工作文件工具读取；此处是防止旧记录或自定义调用绕过对话分流的最后一道检查。
      if (!normalizeMediaType(metadata.mediaType).startsWith('image/')) continue
      requireValue(
        metadata.kind !== 'directory',
        'unsupported_input',
        '文件夹不能直接作为模型附件。'
      )
      const info = { ...metadata, mediaType: normalizeMediaType(metadata.mediaType) }
      const choice = selectAttachmentTransport(
        context.rules,
        context.connection,
        info,
        context.adapter.canUpload
      )
      context.decisions.set(attachment.asset.id, { assetId: attachment.asset.id, ...choice })
      let data = new Uint8Array(0)
      if (!inspect) {
        if (choice.transport === 'file-api') {
          requireValue(this.remoteFiles, 'file_upload_unavailable', '远端文件管理尚未装配。')
          try {
            const record = await this.remoteFiles.ensureUploaded(
              context.adapter,
              attachment.asset,
              signal
            )
            context.records.set(record.id, record)
            context.decisions.set(attachment.asset.id, {
              assetId: attachment.asset.id,
              transport: 'file-api',
              reason: record.reused ? 'reused_reference' : 'uploaded'
            })
            data = copy(record.reference)
          } catch (error) {
            if (
              context.connection.attachmentTransport === 'auto' &&
              choice.inline &&
              uploadUnsupported(error)
            ) {
              context.decisions.set(attachment.asset.id, {
                assetId: attachment.asset.id,
                transport: 'inline',
                reason: 'upload_unsupported'
              })
              data = await this.assets.read(attachment.asset)
            } else {
              if (signal?.aborted || error?.name === 'AbortError' || error?.problem) throw error
              throw failure(
                'file_upload_failed',
                '附件上传失败；本地附件已保留，请检查连接或重试。',
                true
              )
            }
          }
        } else data = await this.assets.read(attachment.asset)
      }
      // 统一使用 SDK 文件内容块，图片 MIME 仍保持为 image/*；厂商引用不当作普通字符串。
      content.push({ type: 'file', data, mediaType: info.mediaType, filename: info.name })
    }
    return content
  }

  async invalidateRejected(request, error) {
    if (!error.remoteRejection || !this.requests.has(request)) return
    const context = await this.requests.get(request)
    if (context.adapter.provider !== 'deepseek' || context.adapter.protocol !== 'chat-completions')
      return
    const records = [...context.records.values()]
    const ids = error.remoteRejection.ids
    for (const record of records) {
      const matches = ids.includes(record.reference.deepseek)
      // 未指出具体引用时，只有本次恰好使用一个远端副本才能安全归因。
      if (matches || (!ids.length && records.length === 1))
        await this.remoteFiles.invalidate(record)
    }
  }

  async diagnostics(request) {
    if (!this.requests.has(request)) return []
    return [...(await this.requests.get(request)).decisions.values()].map(copy)
  }
}
