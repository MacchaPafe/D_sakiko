import { extractAssetReferences } from '../resources/references.js'
import { encodeArchive, decodeArchive } from './portable-archive.js'
import { lastDialogueEntry } from '../../shared/chat-history.js'
import { historicalCharacterNames } from '../conversation/participants.js'
import path from 'node:path'
import { readFile, copyFile, readdir, unlink } from 'node:fs/promises'
import {
  copy,
  requireValue,
  uuid,
  fingerprint,
  readJson,
  writeJson,
  SerialQueue,
  safeId,
  SnapshotBus
} from '../core/support.js'

/** @typedef {import('../../shared/contracts/conversation.js').Chat} Chat */

/**
 * 对话存档管理模块，负责对话数据的持久化读写、版本校验、损坏恢复以及备份导入导出。
 */
export class ChatStore {
  /**
   * 创建一个对话存档管理器。
   * @param {object} options 创建选项。
   * @param {string} options.directory 对话存档文件与索引文件所在的存储目录。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 媒体资源管理器，用于在导入和导出对话备份时打包或还原附件与音频资源。
   */
  constructor({ directory, assets }) {
    this.directory = directory
    this.assets = assets
    this.queue = new SerialQueue()
    this.previewChanges = new SnapshotBus()
    this.manifest = path.join(directory, 'index.json')
    this.assets.registerOwnerReader('chat', async (id) => {
      const loaded = await this.loadCurrent(id)
      requireValue(loaded.status !== 'corrupt', 'corrupt_archive')
      return loaded.status === 'loaded' ? loaded.chat : null
    })
  }

  file(id) {
    return path.join(this.directory, safeId(id) + '.json')
  }

  /** @param {() => void} listener 通知侧栏重新读取已提交的列表。 @returns {() => void} 取消订阅。 */
  observePreviews(listener) {
    // 初次订阅及断线重连时也读取列表，补上尚未建立订阅期间的回复。
    return this.previewChanges.subscribe('index', listener, {})
  }

  /**
   * 读取存档目录的索引文件。
   */
  async index() {
    return readJson(this.manifest, { entries: [] })
  }

  /**
   * 校验对话存档的基础结构与内部提醒 ID 的唯一性。
   */
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

  /**
   * 从完整对话生成索引条目。
   * 索引中额外记录 reminderIds 用于保存时快速校验跨对话的提醒 ID 唯一性，对外返回摘要列表时会剔除该内部字段。
   */
  summary(chat) {
    const entry = lastDialogueEntry(chat)
    const revealAt = chat.meta.lotteries?.find(
      (item) => item.deliveredTurnId === entry?.ref.turnId
    )?.revealAt
    return {
      id: chat.id,
      title: chat.title,
      ...(chat.meta.extensions?.generatedTitle || chat.meta.extensions?.manualTitle
        ? { showTitle: true }
        : {}),
      lastReply: entry?.line.text ?? null,
      lastReplyTranslation: entry?.translation ?? null,
      ...(revealAt > Date.now() ? { lastReplyRevealAt: revealAt } : {}),
      revision: chat.revision,
      characterIds: copy(chat.characterIds),
      characterNames: historicalCharacterNames(chat),
      mode: chat.mode,
      turnCount: chat.turns.length,
      reminderIds: chat.meta.reminders.map((item) => item.id)
    }
  }

  /**
   * 获取指定版本存档对应的文件路径。
   * 每次保存都写入带 revision 的新版本文件，再原子更新 index.json；这样即使写入中途崩溃，未写入索引的残留文件也不会破坏旧版存档。
   */
  versionFile(entry) {
    return path.join(this.directory, safeId(entry.id) + '.' + entry.revision + '.json')
  }

  /**
   * 获取所有已保存对话的摘要列表。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 对话摘要列表。
   */
  async list() {
    return this.queue.run('index', async () => {
      const index = await this.index()
      let changed = false
      for (const entry of index.entries) {
        if (Object.hasOwn(entry, 'lastReply') && Object.hasOwn(entry, 'lastReplyTranslation'))
          continue
        const loaded = await this.loadCurrent(entry.id)
        if (loaded.status !== 'loaded') continue
        const reply = lastDialogueEntry(loaded.chat)
        entry.lastReply = reply?.line.text ?? null
        entry.lastReplyTranslation = reply?.translation ?? null
        changed = true
      }
      // 旧索引只补齐一次，侧栏刷新不再读取全部历史；修复摘要不改变聊天 revision。
      if (changed) await writeJson(this.manifest, index)
      return index.entries.map((entry) => {
        const result = copy(entry)
        delete result.reminderIds
        return result
      })
    })
  }

  /**
   * 加载指定 ID 的对话存档。
   * @param {string} chatId 对话存档 ID。
   * @returns {Promise<import('../../shared/contracts/archive.js').ChatLoadResult>} 加载结果（包含正常加载 loaded、不存在 missing 或文件损坏 corrupt 三种状态）。
   */
  async load(chatId) {
    return this.queue.run('index', () => this.loadCurrent(chatId))
  }

  /** 在存储锁内把已提交快照交给运行 owner，避免加载与删除之间失去保护。 */
  async loadProtected(chatId, owner) {
    return this.queue.run('index', async () => {
      const loaded = await this.loadCurrent(chatId)
      if (loaded.status === 'loaded')
        await this.assets.addReferences(owner, extractAssetReferences(loaded.chat))
      return loaded
    })
  }

  /**
   * 读取指定对话当前已提交的最新版本存档（调用方需已持有 'index' 队列锁）。
   */
  async loadCurrent(chatId) {
    const entry = (await this.index()).entries.find((item) => item.id === chatId)
    if (!entry) return { status: 'missing' }
    const raw = await readFile(this.versionFile(entry), 'utf8')
    try {
      const envelope = JSON.parse(raw)
      // 为什么更高版本的存档直接抛出异常而不是标记为 corrupt：
      // 高版本应用创建的存档只是当前旧版程序无法解析，并非文件损坏，直接拒绝可防止用户触发损坏恢复而误覆盖高版本数据。
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
   * 备份损坏的存档文件，并使用新的对话内容替换恢复该存档。
   * @param {import('../../shared/contracts/archive.js').CorruptArchive} recovery 加载存档时返回的损坏检测信息。
   * @param {Chat} replacement 用于替换的完整对话内容（其 id 必须与原存档一致，且 revision 为 0）。
   * @returns {Promise<{ chat: Chat, backupId: string }>} 恢复后的新对话存档与损坏原文件的备份 ID。
   */
  async recoverCorrupt(recovery, replacement) {
    return this.queue.run('index', async () => {
      this.validate(replacement)
      requireValue(replacement.id === recovery.chatId && replacement.revision === 0)
      const index = await this.index()
      const entry = index.entries.find((item) => item.id === recovery.chatId)
      requireValue(entry, 'recovery_expired')
      const raw = await readFile(this.versionFile(entry), 'utf8')
      // 核对文件指纹，确保待恢复的损坏文件在此期间没有被其他操作更新或恢复过。
      requireValue(
        fingerprint(raw) === recovery.recoveryId &&
          (await this.loadCurrent(recovery.chatId)).status === 'corrupt',
        'recovery_expired'
      )
      const backupId = uuid()
      await copyFile(this.versionFile(entry), path.join(this.directory, backupId + '.backup'))
      await this.assets.assertPersistent(replacement)
      const next = copy(replacement)
      next.revision = entry.revision + 1
      const summary = this.summary(next)
      await this.assets.commitReferences('chat:' + next.id, next, async () => {
        await writeJson(this.versionFile(summary), { version: 1, chat: next })
        index.entries[index.entries.indexOf(entry)] = summary
        await writeJson(this.manifest, index)
      })
      await this.cleanupVersionsUnlocked()
      this.previewChanges.emit('index', {})
      return { chat: next, backupId }
    })
  }

  /**
   * 更新对话存档列表的展示顺序。
   * @param {string[]} chatIds 重新排序后的完整对话 ID 数组。
   * @returns {Promise<void>}
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
      this.previewChanges.emit('index', {})
    })
  }

  /**
   * 保存新建或更新后的对话存档。
   * @param {Chat} snapshot 完整的对话数据快照。新建时 revision 为 0，更新时需传入所基于的 revision 版本号。
   * @returns {Promise<Chat>} 保存成功并递增版本号后的对话数据副本。
   */
  async save(snapshot) {
    snapshot = copy(snapshot)
    // 为什么使用统一的 'index' 锁而不是按 chatId 加锁：
    // 所有对话共享同一个 index.json 且需要校验跨对话提醒 ID 唯一性，统一串行化可防止不同对话并发保存时互相覆盖索引。
    // 此外，存储数据速度是比较快的，所以就算改成串行，应当也不会造成很大的性能损失。
    return this.queue.run('index', async () => {
      this.validate(snapshot)
      await this.assets.assertPersistent(snapshot)
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
      await this.assets.commitReferences('chat:' + next.id, next, async () => {
        await writeJson(this.versionFile(entry), { version: 1, chat: next })
        if (previous) index.entries[index.entries.indexOf(previous)] = entry
        else index.entries.push(entry)
        await writeJson(this.manifest, index)
      })
      await this.cleanupVersionsUnlocked()
      if (
        !previous ||
        previous.lastReply !== entry.lastReply ||
        previous.lastReplyTranslation !== entry.lastReplyTranslation ||
        previous.lastReplyRevealAt !== entry.lastReplyRevealAt ||
        previous.title !== entry.title
      ) {
        this.previewChanges.emit('index', {})
      }
      return copy(next)
    })
  }

  /**
   * 批量交接对话的服装选择。选择只有角色目录 ID，媒体引用和历史保持原存档版本的语义。
   * @param {Array<{id: string, revision: number, characters: import('../../shared/contracts/settings.js').ChatSettings['characters']}>} changes 以当前存档版本为基准的服装选择。
   * @returns {Promise<Chat[]>} 已一起提交的存档。
   */
  async saveOutfitSelections(changes) {
    if (!changes.length) return []
    return this.queue.run('index', async () => {
      const index = await this.index()
      const entries = new Map(index.entries.map((entry, position) => [entry.id, position]))
      const saved = []
      for (const { id, revision, characters } of changes) {
        const position = entries.get(id)
        requireValue(index.entries[position]?.revision === revision, 'revision_conflict')
        const loaded = await this.loadCurrent(id)
        requireValue(loaded.status === 'loaded', 'chat_unavailable', '请先恢复受影响的对话存档。')
        const next = loaded.chat
        next.meta.settings.characters = copy(characters)
        next.revision += 1
        const summary = this.summary(next)
        await writeJson(this.versionFile(summary), { version: 1, chat: next })
        index.entries[position] = summary
        saved.push(next)
      }
      // 索引一次替换；读取者要么看到整组旧选择，要么看到整组新选择。
      await writeJson(this.manifest, index)
      await this.cleanupVersionsUnlocked()
      return saved
    })
  }

  /**
   * 删除指定的对话存档及其索引记录。
   * @param {string} chatId 要删除的对话 ID。
   * @param {number} expectedRevision 当前所基于的版本号，用于防止误删已被更新的存档。
   * @returns {Promise<void>}
   */
  async delete(chatId, expectedRevision) {
    return this.queue.run('index', async () => {
      const index = await this.index()
      const entry = index.entries.find((item) => item.id === chatId)
      requireValue(entry && entry.revision === expectedRevision, 'revision_conflict')
      index.entries = index.entries.filter((item) => item.id !== chatId)
      await this.assets.commitReferences('chat:' + chatId, null, () =>
        writeJson(this.manifest, index)
      )
      this.previewChanges.emit('index', {})
      await this.cleanupVersionsUnlocked()
    })
  }

  /**
   * 将指定的一个或多个对话及其引用的附件、音频资源打包导出为备份文件。
   * @param {string[]} chatIds 要导出的对话 ID 列表。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 导出的备份文件资源引用。
   */
  async exportArchive(chatIds, { includeAudio = true } = {}) {
    return this.queue.run('index', () => this.exportCurrent(chatIds, includeAudio))
  }

  /**
   * 在持有存档队列锁的情况下读取对话并打包导出资源（防止导出读取期间对话被并发删除）。
   */
  async exportCurrent(chatIds, includeAudio) {
    const chats = []
    for (const id of chatIds) {
      const loaded = await this.loadCurrent(id)
      requireValue(loaded.status === 'loaded', 'archive_unavailable')
      chats.push(loaded.chat)
    }
    return this.assets.using(chats, async () => {
      const bytes = await encodeArchive(this.assets, chats, includeAudio)
      const owner = await this.assets.createTemporaryOwner('export', [], 10 * 60 * 1000)
      try {
        return (
          await this.assets.importAsset(
            { kind: 'bytes', name: 'chats.dsakiko-chat.zip', mediaType: 'application/zip', bytes },
            owner,
            'archive'
          )
        ).asset
      } catch (error) {
        await this.assets.releaseWhenPossible(owner)
        throw error
      }
    })
  }

  /**
   * 解析并校验导入的对话备份文件，将其内嵌资源导入媒体库并返回待保存的对话草稿。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 已导入到媒体库的备份文件引用。
   * @returns {Promise<import('../../shared/contracts/archive.js').ArchiveDrafts>} 解析出的对话草稿列表、警告信息及临时资源持有者标识。
   */
  async readArchive(archive, roles = []) {
    return this.assets.using(archive, () => this.importArchive(archive, roles))
  }

  /**
   * 读取备份文件内容并将其中内嵌的媒体资源导入本地资源库。
   */
  async importArchive(archive, roles) {
    const resourceOwner = await this.assets.createTemporaryOwner('import')
    try {
      const result = await decodeArchive(
        this.assets,
        await this.assets.read(archive),
        resourceOwner,
        roles
      )
      for (const chat of result.chats) this.validate(chat)
      return result
    } catch (error) {
      // 导入中途失败时立即释放已创建的临时资源引用，方便垃圾回收清理残留文件。
      await this.assets.releaseWhenPossible(resourceOwner)
      throw error
    }
  }
  /**
   * 清理存档目录中已被新版本取代的旧版存档文件以及中断遗留的临时文件。
   * @returns {Promise<void>}
   */
  async cleanupVersions() {
    return this.queue.run('index', () => this.cleanupVersionsUnlocked())
  }

  /**
   * 根据当前索引记录清理不再使用的历史版本文件与临时文件（调用方需已持有 'index' 队列锁）。
   */
  async cleanupVersionsUnlocked() {
    try {
      const current = new Set(
        (await this.index()).entries.map((entry) => path.basename(this.versionFile(entry)))
      )
      const files = await readdir(this.directory, { withFileTypes: true })
      for (const file of files) {
        if (!file.isFile() || current.has(file.name)) continue
        const versionFile = /^[\p{L}\p{N}_-]{1,100}\.\d+\.json$/u.test(file.name)
        const interruptedWrite =
          /^(?:[\p{L}\p{N}_-]{1,100}\.\d+|index)\.json\.[a-f0-9-]{36}\.tmp$/u.test(file.name)
        if (!versionFile && !interruptedWrite) continue
        await unlink(path.join(this.directory, file.name)).catch((error) => {
          if (error.code !== 'ENOENT') this.assets.reportMaintenance(error)
        })
      }
    } catch (error) {
      if (error.code !== 'ENOENT') this.assets.reportMaintenance(error)
    }
  }
}
