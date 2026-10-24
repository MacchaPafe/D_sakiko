import { ResourceImports } from './resources/imports.js'
import path from 'node:path'
import { Settings } from './settings/interface.js'
import { ChatStore } from './storage/interface.js'
import { CharacterCatalog } from './characters/interface.js'
import { MediaAssets } from './resources/interface.js'
import { Agent } from './llm/interface.js'
import { Speech } from './speech/interface.js'
import { Performance } from './performance/interface.js'
import { Conversations } from './conversation/interface.js'
import { ToolCatalog, Interactions, ReminderWriter } from './tools/interface.js'
import { failure, uuid } from './core/support.js'

/**
 * @typedef {object} CoreBackend
 * @property {Settings} settings 全局设置与模型连接管理。
 * @property {CharacterCatalog} characters 角色与用户对话身份目录。
 * @property {MediaAssets} assets 本地媒体资源管理。
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
 * @param {boolean} [options.scanReminders] 是否启动后台定时扫描到期提醒，默认开启。
 * @param {string} [options.templateDirectory] 自定义的提示词模板目录路径。
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
  templateDirectory
} = {}) {
  const assets = new MediaAssets({ appDataRoot, resourceLibraryRoot, cacheRoot })
  await assets.state()
  const imports = new ResourceImports(assets)
  const settings = new Settings({ directory: appDataRoot, assets })
  const resolveModel = async (presentation) =>
    presentation.mapping
      ? JSON.parse(Buffer.from(await assets.read(presentation.mapping)).toString('utf8'))
      : { motions: {}, expressions: {}, states: {} }
  const characters = new CharacterCatalog({
    directory: appDataRoot,
    resolveModel,
    assets
  })
  const store = new ChatStore({ directory: path.join(appDataRoot, 'chats'), assets })
  await assets.initialize()
  await store.cleanupVersions()
  const performance = injectedPerformance ?? new Performance({ resolveModel, assets })
  performance.assets ??= assets
  const speech = injectedSpeech ?? new Speech({ connect: connectSpeech, assets })
  const agent =
    injectedAgent ??
    new Agent({ credential: (scope) => settings.credential(scope), assets, templateDirectory })
  settings.probe = async (connection, signal) =>
    agent.generate({
      model: agent.model(connection),
      prompt: 'Reply OK.',
      maxOutputTokens: 8,
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
    kinds: {
      question: {
        requestSchema: {
          type: 'object',
          properties: {
            question: { type: 'string', minLength: 1 },
            choices: { type: 'array', items: { type: 'string' } }
          },
          required: ['question'],
          additionalProperties: false
        },
        resultSchema: { type: 'string', minLength: 1 }
      }
    }
  })
  const question = {
    name: 'ask_user',
    description: '向用户提出问题，登记后继续当前对话；答案稍后以新轮次交付。',
    dependencies: [],
    mode: 'async',
    parametersSchema: interactions.kinds.question.requestSchema,
    resultSchema: { type: 'string' },
    requiredCapabilities: ['interactions']
  }
  const reminder = {
    name: 'create_reminder',
    description: '创建到期后主动回复的提醒。',
    dependencies: [],
    mode: 'sync',
    parametersSchema: {
      type: 'object',
      properties: { dueAt: { type: 'string' }, instruction: { type: 'string', minLength: 1 } },
      required: ['dueAt', 'instruction'],
      additionalProperties: false
    },
    resultSchema: { type: 'object' },
    requiredCapabilities: ['reminders']
  }
  const tools = new ToolCatalog({
    interactions,
    reminders: (chatId) =>
      new ReminderWriter((options, source) =>
        conversations.createReminder(chatId, options, source)
      ),
    entries: [
      {
        definition: question,
        frontend: true,
        bind:
          ({ interactions: bound }) =>
          async (payload, source, signal) =>
            bound.request(
              { requestId: source.turnId + ':' + source.callId, kind: 'question', source, payload },
              signal
            )
      },
      {
        definition: reminder,
        bind:
          ({ reminders }) =>
          async (options, source) => ({
            status: 'succeeded',
            value: await reminders.create(options, source)
          })
      }
    ]
  })
  conversations = new Conversations({
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
      hostCapabilities: ['interactions', 'reminders']
    }
  })
  conversations.interactions = interactions
  // 上一轮扫描尚未完成时直接跳过本次定时器回调，防止慢操作导致并发扫描叠加。
  let scanning = false
  const scan = async () => {
    if (scanning) return
    scanning = true
    try {
      for (const item of await conversations.listReminders())
        if (item.state === 'enabled')
          await conversations.deliverReminder(item.chatId, item.id, item.id).catch(() => {})
    } finally {
      scanning = false
    }
  }
  const timer = scanReminders ? setInterval(() => scan().catch(() => {}), 1000) : null
  timer?.unref()
  const maintenance = setInterval(
    () => assets.maintain().catch((error) => assets.reportMaintenance(error)),
    30000
  )
  maintenance.unref()
  return {
    imports,
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
      interactions.close()
      await performance.close()
      await speech.close?.()
      await assets.maintain()
    }
  }
}
