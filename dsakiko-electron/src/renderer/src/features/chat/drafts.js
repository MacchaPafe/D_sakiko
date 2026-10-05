/** 文本原型的草稿所有者；发送确认不会清除发送期间新增的内容。 */
export class Drafts {
  drafts = new Map()
  get(chatId) {
    return this.drafts.get(chatId)?.text || ''
  }
  update(chatId, text) {
    const previous = this.drafts.get(chatId)
    if (previous?.text === text) return
    this.drafts.set(chatId, { text, requestId: crypto.randomUUID() })
  }
  prepare(chatId) {
    const draft = this.drafts.get(chatId)
    if (!draft?.text.trim()) throw new Error('请先输入内容。')
    return { requestId: draft.requestId, submission: { text: draft.text, attachments: [] } }
  }
  acknowledge(chatId, requestId) {
    if (this.drafts.get(chatId)?.requestId === requestId) this.drafts.delete(chatId)
  }
}
