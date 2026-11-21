/** @typedef {{id:string,name:string,file?:File,preview?:string,state:string,attachment?:object,error?:string,draftId?:string}} DraftFile 会话草稿的一项附件。 */
/** 草稿按会话持有；每个文件有独立导入身份，迟到的结果无法恢复已删除项。 */
export class AttachmentDraft {
  constructor(client, initial = []) {
    this.client = client
    this.items = initial.map((attachment) => ({
      id: crypto.randomUUID(),
      state: 'ready',
      name: attachment.name,
      attachment
    }))
    this.listeners = new Set()
  }
  subscribe = (listener) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  getSnapshot = () => this.items
  publish() {
    this.items = [...this.items]
    for (const notify of this.listeners) notify()
  }
  /** 复用本会话工作目录内已有的文件；持久资源不需要新的草稿租约。 */
  attachExisting(attachment) {
    if (this.items.some((item) => item.attachment?.asset.id === attachment.asset.id)) return
    this.items.push({ id: crypto.randomUUID(), name: attachment.name, attachment, state: 'ready' })
    this.publish()
  }
  add(files) {
    for (const file of files) {
      const item = {
        id: crypto.randomUUID(),
        file,
        name: file.name,
        state: 'importing',
        preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : null
      }
      this.items.push(item)
      void this.import(item)
    }
    this.publish()
  }
  async import(item) {
    item.state = 'importing'
    this.publish()
    try {
      if (item.file.size > 64 * 1024 * 1024) throw new Error('单个附件最多 64 MiB。')
      const { draftId } = await this.client.assets.createDraft()
      item.draftId = draftId
      const options = {
        draftId,
        name: item.name,
        mediaType: item.file.type || 'application/octet-stream'
      }
      let info
      if (item.file.size > 5 * 1024 * 1024 && window.dsakikoHost?.importAttachment) {
        // 小文件复用现有上传接口。大文件不经过 JSON 请求体与 Base64 编码。
        const bytes = new Uint8Array(await item.file.arrayBuffer())
        info = await window.dsakikoHost.importAttachment({ ...options, bytes })
      } else {
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result.split(',')[1])
          reader.onerror = () => reject(reader.error)
          reader.readAsDataURL(item.file)
        })
        info = await this.client.assets.upload({ ...options, base64: data, purpose: 'attachment' })
      }
      item.attachment = { asset: info.asset, name: info.name, mediaType: info.mediaType }
      item.state = 'ready'
    } catch (error) {
      item.state = 'failed'
      item.error = error.message
    } finally {
      if (item.draftId && (!this.items.includes(item) || item.state === 'failed')) {
        await this.client.assets.discardDraft(item.draftId).catch(() => {})
        item.draftId = null
      }
      this.publish()
    }
  }
  remove(item) {
    this.items = this.items.filter((other) => other !== item)
    if (item.preview) URL.revokeObjectURL(item.preview)
    if (item.draftId && item.state !== 'importing')
      void this.client.assets.discardDraft(item.draftId).catch(() => {})
    this.publish()
  }
  clear() {
    for (const item of [...this.items]) this.remove(item)
  }
}
