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
 * @property {Settings} settings 全局设置与私有模型连接。
 * @property {CharacterCatalog} characters 角色与人格目录。
 * @property {MediaAssets} assets 资源引用与文件交接。
 * @property {ChatStore} store 存档能力，只交给可信后端。
 * @property {Performance} performance 不依赖客户端的演出计划。
 * @property {Speech} speech Python 语音任务接口。
 * @property {Agent} agent 模型生成与工具循环。
 * @property {Conversations} conversations 对话业务入口及唯一运行时修改所有者。
 * @property {ToolCatalog} tools 工具目录与权限绑定。
 * @property {Interactions} interactions 多客户端共享的待处理交互。
 * @property {() => Promise<void>} close 取消任务并释放计时器，不删除存档。
 */

/**
 * 装配正式接口实现；不依赖旧原型协议、Electron 或任何 React 页面。
 * @param {object} options dataRoot 必须由可信宿主指定；外部能力可在测试或其他宿主替换。
 * @param {string} options.dataRoot 正式存档和资源的根目录，不自动导入原型数据。
 * @param {() => Promise<import('./speech/python-transport.js').SpeechConnection>} [options.connectSpeech] 宿主提供的 Python 就绪能力，首次合成才使用。
 * @param {Agent} [options.agent] 可替换的生成接口。
 * @param {Performance} [options.performance] 可替换的后端计划时钟。
 * @param {Speech} [options.speech] 可替换的语音接口。
 * @param {boolean} [options.scanReminders] 是否启用提醒扫描，确定性测试可关闭。
 * @param {string} [options.templateDirectory] 可信宿主指定的内置模板目录。
 * @returns {Promise<CoreBackend>} 完整后端；HTTP 仅暴露白名单中的业务方法。
 */
export async function createCoreBackend({
  dataRoot,
  connectSpeech = async () => {
    throw failure('speech_unavailable', '语音服务未启动。')
  },
  agent: injectedAgent,
  performance: injectedPerformance,
  speech: injectedSpeech,
  scanReminders = true,
  templateDirectory
} = {}) {
  const assets = new MediaAssets({ directory: path.join(dataRoot, 'assets') })
  const settings = new Settings({ directory: dataRoot })
  const resolveModel = async (presentation) =>
    presentation.mapping
      ? JSON.parse(Buffer.from(await assets.read(presentation.mapping)).toString('utf8'))
      : { motions: {}, expressions: {}, states: {} }
  const characters = new CharacterCatalog({ directory: dataRoot, resolveModel })
  const store = new ChatStore({ directory: path.join(dataRoot, 'chats'), assets })
  const performance = injectedPerformance ?? new Performance({ resolveModel })
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
  // 交互能力只持有来源校验和结果入队函数，不能直接改 Chat 或替用户发起任意轮次。
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
  // 上一轮扫描尚未完成时跳过下一次 tick；提醒是否到期和是否已投递仍由 Conversations 裁定。
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
  return {
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
    /** 先撤销提醒与对话任务，再释放交互和演出等待。
     * @returns {Promise<void>} 后端持有的运行资源已清理。
     */
    async close() {
      clearInterval(timer)
      await conversations.close()
      interactions.close()
      await performance.close()
    }
  }
}
