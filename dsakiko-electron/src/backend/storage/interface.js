import path from 'node:path'
import { readFile, copyFile } from 'node:fs/promises'
import {
  copy,
  requireValue,
  uuid,
  fingerprint,
  readJson,
  writeJson,
  SerialQueue,
  safeId
} from '../core/support.js'

/** @typedef {import('../../shared/contracts/conversation.js').Chat} Chat */

/**
 * Chat 存储模块：串行化同一存档的写入，负责原子落盘、格式校验、版本迁移和备份导入导出。
 * 需要：携带所基于版本的完整 Chat 快照、删除时的预期版本、资源引用与明确的存储位置配置。
 * 不需要：Agent、演出进度、TTS、哪条消息应该删除或重新生成的业务判断。
 * Chat 是持久化数据，Conversation 是它的运行时修改所有者；本模块只负责 Chat 的持久化。
 * 版本校验防止旧快照覆盖新存档，不承担鉴权或判断异步业务结果是否仍有效的职责。
 * 业务实现由宿主注入存储位置与外部能力。
 */
export class ChatStore {
  /**
   * 绑定存档目录与归档资源能力；索引是成功提交的唯一入口。
   * @param {object} options 宿主依赖。
   * @param {string} options.directory 存档版本和索引目录。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 导入导出使用的资源接口。
   */
  constructor({ directory, assets }) {
    this.directory = directory
    this.assets = assets
    this.queue = new SerialQueue()
    this.manifest = path.join(directory, 'index.json')
  }
  /**
   * 收集结构化材料中的 AssetRef；普通带 id 的消息不是资源引用。
   * @param {unknown} value Chat 或归档材料。
   * @returns {import('../../shared/contracts/common.js').AssetRef[]} 按身份去重的引用；AssetRef 必须保持仅含 id 的形状。
   */
  references(value) {
    const found = new Map()
    function visit(item) {
      if (!item || typeof item !== 'object') return
      if (!Array.isArray(item) && Object.keys(item).length === 1 && typeof item.id === 'string')
        found.set(item.id, { id: item.id })
      else Object.values(item).forEach(visit)
    }
    visit(value)
    return [...found.values()]
  }
  file(id) {
    return path.join(this.directory, safeId(id) + '.json')
  }
  /** 读取提交索引；未被索引引用的版本文件不是可见存档。 */
  async index() {
    return readJson(this.manifest, { entries: [] })
  }
  /** 校验本版存储必需结构与同 Chat 提醒唯一性，不替代 Conversations 的业务校验。 */
  validate(chat) {
    requireValue(
      chat && typeof chat.id === 'string' && Number.isInteger(chat.revision) && chat.revision >= 0
    )
    safeId(chat.id)
    requireValue(
      Array.isArray(chat.turns) &&
        Array.isArray(chat.characterIds) &&
        Array.isArray(chat.meta?.reminders)
    )
    requireValue(
      new Set(chat.meta.reminders.map((item) => item.id)).size === chat.meta.reminders.length,
      'duplicate_reminder'
    )
  }
  /** 索引保留提醒身份用于跨 Chat 唯一性校验，对外列表会移除此私有字段。 */
  summary(chat) {
    return {
      id: chat.id,
      title: chat.title,
      revision: chat.revision,
      characterIds: copy(chat.characterIds),
      mode: chat.mode,
      turnCount: chat.turns.length,
      reminderIds: chat.meta.reminders.map((item) => item.id)
    }
  }
  // 先写不可变版本，再原子替换索引；崩溃留下的未引用版本不会被当成成功提交。
  versionFile(entry) {
    return path.join(this.directory, safeId(entry.id) + '.' + entry.revision + '.json')
  }

  /**
   * 读取摘要列表，不加载所有对话内容。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 存档入口。
   */
  async list() {
    return (await this.index()).entries.map((entry) => {
      const result = copy(entry)
      delete result.reminderIds
      return result
    })
  }

  /**
   * 加载并迁移本应用版本，保留未知扩展字段；旧 Python 文件由独立导入适配器一次转换，核心不长期读取旧索引。
   * 缺少资源保留引用并报告警告，不阻止可读历史；权限/临时 I/O/较新格式拒绝，不标为损坏。
   * 保留 userPersona 中已固定的文本，不查询当前身份目录；旧存档仅在缺少身份信息时迁移为 null。
   * @param {string} chatId 存档身份。
   * @returns {Promise<import('../../shared/contracts/archive.js').ChatLoadResult>} 正常、缺失或损坏检测结果，不自动覆盖。
   */
  async load(chatId) {
    const entry = (await this.index()).entries.find((item) => item.id === chatId)
    if (!entry) return { status: 'missing' }
    const raw = await readFile(this.versionFile(entry), 'utf8')
    try {
      const envelope = JSON.parse(raw)
      requireValue(envelope.version <= 1, 'newer_archive')
      this.validate(envelope.chat)
      return { status: 'loaded', chat: copy(envelope.chat), warnings: [] }
    } catch (error) {
      if (error.problem?.code === 'newer_archive') throw error
      return {
        status: 'corrupt',
        recovery: {
          chatId,
          recoveryId: fingerprint(raw),
          problem: { code: 'corrupt_archive', message: '存档格式损坏。', retryable: false }
        }
      }
    }
  }

  /**
   * 在保留损坏原件备份的前提下，用指定内容恢复该对话存档。
   * 重新核对损坏文件后先完整备份成功，再创建替代存档；备份失败绝不覆盖，保守保留备份所需资源。
   * @param {import('../../shared/contracts/archive.js').CorruptArchive} recovery 本次检测身份。
   * @param {Chat} replacement 同一 chatId、revision 0 的替代内容；只接受损坏恢复分支，不借此绕过正常 revision 冲突。
   * @returns {Promise<{ chat: Chat, backupId: string }>} 已保存替代与可追溯备份身份。
   */
  async recoverCorrupt(recovery, replacement) {
    return this.queue.run('index', async () => {
      this.validate(replacement)
      requireValue(replacement.id === recovery.chatId && replacement.revision === 0)
      const index = await this.index()
      const entry = index.entries.find((item) => item.id === recovery.chatId)
      requireValue(entry, 'recovery_expired')
      const raw = await readFile(this.versionFile(entry), 'utf8')
      requireValue(
        fingerprint(raw) === recovery.recoveryId &&
          (await this.load(recovery.chatId)).status === 'corrupt',
        'recovery_expired'
      )
      const backupId = uuid()
      await copyFile(this.versionFile(entry), path.join(this.directory, backupId + '.backup'))
      const next = copy(replacement)
      next.revision = entry.revision + 1
      const summary = this.summary(next)
      await writeJson(this.versionFile(summary), { version: 1, chat: next })
      index.entries[index.entries.indexOf(entry)] = summary
      await writeJson(this.manifest, index)
      return { chat: next, backupId }
    })
  }

  /**
   * 保存对话存档列表的完整排序。
   * @param {string[]} chatIds 完整排序 ID，原子验证无重复/遗漏；与创建/删除索引更新串行。
   * @returns {Promise<void>} 仅更新独立索引，不读取/改写全部 Chat。
   */
  async setOrder(chatIds) {
    return this.queue.run('index', async () => {
      const index = await this.index()
      requireValue(
        chatIds.length === index.entries.length &&
          new Set(chatIds).size === chatIds.length &&
          chatIds.every((id) => index.entries.some((item) => item.id === id))
      )
      index.entries = chatIds.map((id) => index.entries.find((item) => item.id === id))
      await writeJson(this.manifest, index)
    })
  }

  /**
   * 将完整 Chat 快照持久化为新存档或当前存档的新版本。
   * snapshot.revision 表示本次修改所基于的持久化版本，调用方不预先递增。
   * 新建使用 revision 0，仅当该 ID 不存在时接受，首次成功保存为版本 1。
   * 更新时版本比较与写入是同一原子操作：必须匹配当前存档版本，再由本模块递增一次。
   * 冲突拒绝，不覆盖较新的存档；未成功提交不递增，不修改传入对象。
   * 提醒 ID 必须全应用唯一；拒绝同一 Chat 内重复或与其他 Chat 冲突的提醒 ID。
   * @param {Chat} snapshot 已完成业务修改、仍携带原版本的完整快照。
   * @returns {Promise<Chat>} 已落盘且携带新 revision 的独立快照，作为后续修改的基础。
   */
  async save(snapshot) {
    snapshot = copy(snapshot)
    // 所有 Chat 共用一个索引，因此锁索引而非只锁 chatId，避免不同对话互相覆盖索引更新。
    return this.queue.run('index', async () => {
      this.validate(snapshot)
      const index = await this.index()
      const previous = index.entries.find((item) => item.id === snapshot.id)
      requireValue(snapshot.revision === (previous?.revision ?? 0), 'revision_conflict')
      const reminders = new Set(
        index.entries.filter((item) => item.id !== snapshot.id).flatMap((item) => item.reminderIds)
      )
      requireValue(
        snapshot.meta.reminders.every((item) => !reminders.has(item.id)),
        'duplicate_reminder'
      )
      const next = copy(snapshot)
      next.revision += 1
      const entry = this.summary(next)
      await writeJson(this.versionFile(entry), { version: 1, chat: next })
      if (previous) index.entries[index.entries.indexOf(previous)] = entry
      else index.entries.push(entry)
      await writeJson(this.manifest, index)
      return copy(next)
    })
  }

  /**
   * 删除指定对话的持久化存档及其索引入口。
   * 版本比较与删除是同一原子操作；共享附件和音频不在此直接删除。
   * 此操作不携带 Chat 快照，因此显式提供所基于的版本，不能用内部最新值替代。
   * @param {string} chatId 存档身份。
   * @param {number} expectedRevision 调用方所基于的持久化版本，必须为正整数。
   * @returns {Promise<void>} 存档已删除；版本冲突拒绝。
   */
  async delete(chatId, expectedRevision) {
    return this.queue.run('index', async () => {
      const index = await this.index()
      const entry = index.entries.find((item) => item.id === chatId)
      requireValue(entry && entry.revision === expectedRevision, 'revision_conflict')
      index.entries = index.entries.filter((item) => item.id !== chatId)
      await writeJson(this.manifest, index)
    })
  }

  /**
   * 导出可恢复的存档及其必要附件、语音资源，不打包大型模型和 API 凭据。
   * 包含 Chat 的 userPersona 快照与其头像资源，不要求导出来源人格定义或角色目录。
   * @param {string[]} chatIds 待导出的存档。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 可供宿主保存或客户端下载的归档资源。
   */
  async exportArchive(chatIds) {
    const chats = []
    for (const id of chatIds) {
      const loaded = await this.load(id)
      requireValue(loaded.status === 'loaded', 'archive_unavailable')
      chats.push(loaded.chat)
    }
    const resources = []
    for (const ref of this.references(chats)) {
      const info = await this.assets.inspect(ref)
      resources.push({ info, bytes: Buffer.from(await this.assets.read(ref)).toString('base64') })
    }
    const bytes = Buffer.from(JSON.stringify({ format: 'dsakiko', version: 1, chats, resources }))
    return (
      await this.assets.importAsset(
        { kind: 'bytes', name: 'chats.dsakiko.json', mediaType: 'application/json', bytes },
        'export:' + uuid()
      )
    ).asset
  }

  /**
   * 读取并校验归档，返回已迁移的数据草稿；不直接覆盖正在运行的对话。
   * 草稿保留归档中的身份与版本；Conversation 分配新身份并将 revision 设为 0 后，才能作为新 Chat 保存。
   * Conversation 为复制提醒分配全局唯一新 ID、改写相关引用并停用；保存时机也由其决定，失败时回收未交付的临时资源。
   * userPersona 快照及其资源引用随归档恢复，不根据来源编号重新获取或覆盖人格文本。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 已导入的归档文件。
   * @returns {Promise<import('../../shared/contracts/archive.js').ArchiveDrafts>} 新格式草稿、结构化警告及临时资源所有者。
   */
  async readArchive(archive) {
    const envelope = JSON.parse(Buffer.from(await this.assets.read(archive)).toString('utf8'))
    requireValue(
      envelope.format === 'dsakiko' && envelope.version === 1 && Array.isArray(envelope.chats),
      'invalid_archive'
    )
    envelope.chats.forEach((chat) => this.validate(chat))
    const resourceOwner = 'import:' + uuid()
    const replacements = new Map()
    try {
      for (const resource of envelope.resources ?? []) {
        const info = await this.assets.importAsset(
          {
            kind: 'bytes',
            name: resource.info.name,
            mediaType: resource.info.mediaType,
            bytes: Buffer.from(resource.bytes, 'base64')
          },
          resourceOwner
        )
        replacements.set(resource.info.asset.id, info.asset.id)
      }
      function remap(value) {
        if (Array.isArray(value)) return value.map(remap)
        if (value && typeof value === 'object') {
          if (Object.keys(value).length === 1 && replacements.has(value.id))
            return { id: replacements.get(value.id) }
          return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, remap(item)]))
        }
        return value
      }
      return { chats: remap(envelope.chats), warnings: [], resourceOwner }
    } catch (error) {
      await this.assets.setReferences(resourceOwner, [])
      throw error
    }
  }
}
