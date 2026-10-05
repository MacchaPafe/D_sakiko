import { join } from 'node:path'
import { stat } from 'node:fs/promises'
import { Settings } from './settings/settings.js'
import { Assets } from './resources/assets.js'
import { CharacterCatalog } from './characters/catalog.js'
import { JsonStore } from './storage/files.js'
import { Agent } from './llm/agent.js'
import { Speech } from './speech/client.js'
import { Conversations } from './conversation/conversations.js'
import { problemError, toProblem } from '../shared/contracts/desktop.js'

/**
 * 装配独立 Node 业务，Electron 路径和进程能力由宿主注入。
 * @param {object} options 数据目录、演出代理与语音连接工厂。
 * @returns {Promise<object>} 已就绪的具名业务入口。
 */
export async function createBackend({
  dataRoot,
  defaults = {},
  performance,
  connectSpeech,
  restartSpeech,
  emit = () => {}
}) {
  const settings = await new Settings(dataRoot, defaults).initialize()
  const assets = await new Assets(dataRoot, () => settings.get().resourceRoot).initialize()
  const catalog = new CharacterCatalog(dataRoot, assets, () => settings.get().resourceRoot)
  let startupProblem = null
  try {
    await catalog.refresh()
  } catch (error) {
    startupProblem = toProblem(error)
  }
  const speech = new Speech(() => connectSpeech(settings.get()), assets)
  const conversations = await new Conversations({
    store: new JsonStore(join(dataRoot, 'chats')),
    catalog,
    agent: new Agent(() => settings.get()),
    speech,
    performance,
    getSettings: () => settings.get()
  }).initialize()
  conversations.observeAll((snapshot) => emit({ type: 'conversation', snapshot }))
  return {
    assets,
    settings,
    conversations,
    getRuntimeInfo: () => ({ status: 'ready' }),
    async getBootstrap() {
      return {
        characters: await catalog.list(),
        personas: await catalog.listPersonas(),
        conversations: conversations.allSnapshots(),
        settings: settings.publicValue(),
        problem: startupProblem
      }
    },
    getSettings: () => settings.publicValue(),
    async saveSettings(changes) {
      return conversations.configure(async () => {
        if (!changes || typeof changes !== 'object')
          throw problemError('invalid_settings', '设置无效。')
        const before = settings.get()
        const structuralKeys = [
          'resourceRoot',
          'pythonExecutable',
          'device',
          'maxConcurrent',
          'maxResident'
        ]
        const restart = structuralKeys.some(
          (key) => Object.hasOwn(changes, key) && before[key] !== changes[key]
        )
        if (restart && conversations.isBusy())
          throw problemError('chat_busy', '请先停止正在进行的对话，再修改资源或语音运行设置。')
        if (changes.resourceRoot && changes.resourceRoot !== before.resourceRoot) {
          const directory = await stat(join(changes.resourceRoot, 'live2d_related')).catch(
            () => null
          )
          if (!directory?.isDirectory())
            throw problemError('missing_resources', '所选目录不包含 live2d_related，设置未保存。')
        }
        const value = await settings.save(changes)
        if (restart) {
          await restartSpeech()
          await conversations.interruptAll()
          await catalog.refresh()
          startupProblem = null
        }
        emit({ type: 'catalog' })
        return value
      })
    },
    async importLegacySettings() {
      return conversations.configure(async () => {
        if (conversations.isBusy())
          throw problemError('chat_busy', '请先停止正在进行的对话，再读取旧配置。')
        const result = await settings.importLegacy()
        await restartSpeech()
        return result
      })
    },
    listCharacters: () => catalog.list(),
    async setCharacterModel(characterId, modelId) {
      try {
        await conversations.setCharacterModel(characterId, modelId)
      } finally {
        // 选择可能已持久化而窗口随后断连，仍通知界面重新读取实际设置。
        emit({ type: 'catalog' })
      }
      return catalog.list()
    },
    listPersonas: () => catalog.listPersonas(),
    getPersona: (id) => catalog.getPersona(id),
    async savePersona(definition) {
      const result = await catalog.savePersona(definition)
      emit({ type: 'catalog' })
      return result
    },
    listChats: () => conversations.listChats(),
    createChat: (options) => conversations.createChat(options),
    createTurn: (id, submission, options) => conversations.createTurn(id, submission, options),
    stopTurn: (id, turnId) => conversations.stopTurn(id, turnId),
    selectConversation: (id) => conversations.selectConversation(id),
    replayMessage: (id, messageId) => conversations.replayMessage(id, messageId),
    async deleteChat(id) {
      await conversations.deleteChat(id)
      emit({ type: 'deleted', chatId: id })
    },
    resolveAsset: (asset) => assets.resolveAccess(asset),
    async close() {
      // 渲染端无响应时，也要立即开始关闭语音进程，不能等 IPC 超时。
      await Promise.allSettled([conversations.interruptAll(), restartSpeech()])
    }
  }
}
