import { SpeechResources } from './speech/resources.js'
import { ResourceImports } from './resources/imports.js'
import { RemoteFiles } from './remote-files/interface.js'
import { createFileAdapter } from './remote-files/adapter.js'
import path from 'node:path'
import { Settings } from './settings/interface.js'
import { ChatStore } from './storage/interface.js'
import { CharacterCatalog } from './characters/interface.js'
import { resolveModelCatalog } from './characters/model-catalog.js'
import { MediaAssets } from './resources/interface.js'
import { Agent } from './llm/interface.js'
import { Speech } from './speech/interface.js'
import { Performance } from './performance/interface.js'
import { Conversations } from './conversation/interface.js'
import { ToolCatalog, Interactions, ReminderWriter } from './tools/interface.js'
import { createBuiltinTools, interactionKinds } from './tools/builtins.js'
import { failure, uuid } from './core/support.js'

/**
 * @typedef {object} CoreBackend
 * @property {Settings} settings 全局设置与模型连接管理。
 * @property {CharacterCatalog} characters 角色与用户对话身份目录。
 * @property {MediaAssets} assets 本地媒体资源管理。
 * @property {RemoteFiles} remoteFiles 后端私有的远端上传与清理记录。
 * @property {SpeechResources} speechResources GPT-SoVITS 显式文件路径与运行引用。
 * @property {ResourceImports} imports 资源草稿与长期资源库导入管理。
 * @property {ChatStore} store 对话存档管理。
 * @property {Performance} performance 演出时间线与状态管理。
 * @property {Speech} speech 语音合成任务管理。
 * @property {Agent} agent LLM 文本生成与工具调用。
 * @property {Conversations} conversations 对话业务管理。
 * @property {ToolCatalog} tools 可用工具目录。
 * @property {Interactions} interactions 待处理的前端交互请求管理。
 * @property {() => Promise<void>} close 关闭后端，停止后台任务并释放计时器。
 */

/**
 * 创建并组装完整的核心后端实例。
 * @param {object} [options] 创建选项。
 * @param {string} options.appDataRoot 应用数据根目录。
 * @param {string} options.resourceLibraryRoot 模型与音频资源库根目录。
 * @param {string} options.cacheRoot 缓存与临时文件根目录。
 * @param {() => Promise<import('./speech/python-transport.js').SpeechConnection>} [options.connectSpeech] 获取 Python 语音服务连接的函数，在首次合成语音时调用。
 * @param {Agent} [options.agent] 自定义的 Agent 实例（通常用于测试替换）。
 * @param {Performance} [options.performance] 自定义的演出管理实例（通常用于测试替换）。
 * @param {Speech} [options.speech] 自定义的语音管理实例（通常用于测试替换）。
 * @param {boolean} [options.scanReminders] 是否启动后台扫描到期提醒与待交付抽签结果，默认开启。
 * @param {string} [options.templateDirectory] 自定义的提示词模板目录路径。
 * @param {typeof fetch} [options.fetch] 模型、文件与联网工具共用的网络传输，测试可替换。
 * @returns {Promise<CoreBackend>} 组装完成的后端实例。
 */
export async function createCoreBackend({
  appDataRoot,
  resourceLibraryRoot,
  cacheRoot,
  connectSpeech = async () => {
    throw failure('speech_unavailable', '语音服务未启动。')
  },
  agent: injectedAgent,
  performance: injectedPerformance,
  speech: injectedSpeech,
  scanReminders = true,
  templateDirectory,
  fetch: transport = fetch
} = {}) {
  const assets = new MediaAssets({ appDataRoot, resourceLibraryRoot, cacheRoot })
  await assets.state()
  const imports = new ResourceImports(assets)
  const settings = new Settings({ directory: appDataRoot, assets })
  const speechResources = new SpeechResources({ assets, settings })
  // mapping 可直接引用 Python 的 performance.json（如 motions.nod = "点头赞同"）。
  // 模型入口提供真实资源，标注只补充语义；角色能力与演出共用同一个解析器。
  const resolveModel = (presentation) => resolveModelCatalog(assets, presentation)
  const characters = new CharacterCatalog({
    directory: appDataRoot,
    resolveModel,
    assets
  })
  const store = new ChatStore({ directory: path.join(appDataRoot, 'chats'), assets })
  await assets.initialize()
  await store.cleanupVersions()
  const remoteFiles = new RemoteFiles({
    directory: path.join(appDataRoot, 'remote-files'),
    assets,
    adapters: async () => {
      const state = await settings.state()
      return Object.values(state.connections).map((connection) =>
        createFileAdapter(connection, state.secrets[connection.id] ?? '', transport)
      )
    }
  })
  await remoteFiles.initialize()
  const performance = injectedPerformance ?? new Performance({ resolveModel, assets })
  performance.assets ??= assets
  const speech = injectedSpeech ?? new Speech({ connect: connectSpeech, assets })
  const agent =
    injectedAgent ??
    new Agent({
      credential: (scope) => settings.credential(scope),
      assets,
      remoteFiles,
      fetch: transport,
      templateDirectory
    })
  settings.probe = async (connection, signal) =>
    agent.generate({
      model: agent.model(connection),
      providerOptions:
        connection.protocol === 'responses'
          ? { openai: { store: false, include: ['reasoning.encrypted_content'] } }
          : undefined,
      prompt: 'Reply OK.',
      maxOutputTokens: 500,
      maxRetries: 0,
      abortSignal: signal ?? AbortSignal.timeout(15000)
    })
  // 为什么不把 Conversations 实例直接传给 Interactions：
  // 仅向 Interactions 暴露校验和结果入队回调，避免它越权直接修改对话存档或随意发起新轮次。
  let conversations
  const interactions = new Interactions({
    qualify: (request) => conversations.qualifyInteraction(request),
    isValid: (registration) => conversations.validInteraction(registration),
    accept: (completion, registration, request) =>
      conversations.queueInteraction(completion, registration, request),
    kinds: interactionKinds
  })
  const tools = new ToolCatalog({
    interactions,
    reminders: (chatId) =>
      new ReminderWriter((options, source) =>
        conversations.createReminder(chatId, options, source)
      ),
    entries: createBuiltinTools({
      settings,
      assets,
      transport,
      writeFile: (...args) => conversations.writeToolFile(...args),
      live2d: {
        list: (...args) => conversations.listToolModels(...args),
        change: (...args) => conversations.changeToolModel(...args)
      },
      lottery: { create: (...args) => conversations.createLottery(...args) }
    })
  })
  conversations = new Conversations({
    remoteFiles,
    store,
    settings,
    characters,
    agent,
    speech,
    performance,
    assets,
    tools,
    access: {
      readableRoots: [],
      outputLocation: uuid(),
      hostCapabilities: ['interactions', 'reminders', 'files', 'system', 'network', 'live2d']
    }
  })
  conversations.interactions = interactions
  // 上一轮扫描尚未完成时直接跳过本次定时器回调，防止慢操作导致并发扫描叠加。
  let scanning = false
  const scan = async () => {
    if (scanning) return
    scanning = true
    try {
      for (const chat of await conversations.listChats())
        await conversations
          .deliverLotteries(chat.id)
          .catch((error) => assets.reportMaintenance(error))
      for (const item of await conversations.listReminders())
        if (item.state === 'enabled')
          await conversations.deliverReminder(item.chatId, item.id, item.id).catch(() => {})
    } finally {
      scanning = false
    }
  }
  const timer = scanReminders ? setInterval(() => scan().catch(() => {}), 1000) : null
  timer?.unref()
  // 定期（30 秒）维护资源信息，清除过期的所有者信息
  let maintenanceTask = null
  const maintain = () => {
    if (maintenanceTask) return maintenanceTask
    maintenanceTask = (async () => {
      try {
        await assets.maintain()
        await remoteFiles.collectUnused()
      } catch (error) {
        assets.reportMaintenance(error)
      } finally {
        maintenanceTask = null
      }
    })()
    return maintenanceTask
  }
  const maintenance = setInterval(maintain, 30000)
  maintenance.unref()
  return {
    remoteFiles,
    imports,
    speechResources,
    settings,
    characters,
    assets,
    store,
    performance,
    speech,
    agent,
    conversations,
    tools,
    interactions,
    /**
     * 关闭后端并清理运行中的任务与定时器。
     * @returns {Promise<void>}
     */
    async close() {
      clearInterval(timer)
      clearInterval(maintenance)
      await conversations.close()
      await remoteFiles.close()
      await maintenanceTask
      interactions.close()
      await performance.close()
      await speech.close?.()
      await assets.maintain()
    }
  }
}
