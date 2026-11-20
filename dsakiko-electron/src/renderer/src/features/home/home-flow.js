/** @typedef {{characterId: string|null, chatId: string|null, tab: 'single-character'|'scripted-dialogue'}} HomeTarget 稳定导航身份，与尚未创建的聊天草稿分离。 */
/** @typedef {{connectionId: string, modelId: string}} ModelSelection 可持久化的自有模型选择。 */
/** @typedef {{formId: string, model: import('../../../../shared/contracts/characters.js').CharacterModel}} DraftOutfit 尚未建立对话时选中的舞台服装。 */
/** @typedef {{text: string, pending: boolean, intent: object|null, chatId?: string, model?: ModelSelection|null, outfit?: DraftOutfit|null}} InputSession 单次应用内的草稿和可重试发送意图。 */
/**
 * @typedef {object} HomeState 页面只读快照；草稿不写入导航存储。
 * @property {boolean} loading 初次读取尚未完成。
 * @property {object[]} characters 正式角色目录摘要。
 * @property {import('../../../../shared/contracts/conversation.js').ChatSummary[]} chats 正式对话列表。
 * @property {HomeTarget} target 当前导航身份。
 * @property {import('../../../../shared/contracts/conversation.js').ConversationSnapshot|null} snapshot 当前对话的权威观察快照。
 * @property {import('../../../../shared/contracts/settings.js').GlobalSettings|null} settings 全局设置。
 * @property {import('../../../../shared/contracts/models.js').ConnectionDefinition[]} connections 不含凭据的连接目录。
 * @property {import('../../../../shared/contracts/notices.js').Notice[]} notices 按持久顺序排列的通知。
 * @property {string} draft 当前输入会话文本。
 * @property {DraftOutfit|null} draftOutfit 新对话暂选的服装，首轮受理时交给正式对话。
 * @property {boolean} sending 正在等待本次发送的受理结果。
 * @property {string|null} problem 可展示的命令错误。
 */
const NAVIGATION_KEY = 'dsakiko.home.navigation.v1'
const RECENT_MODEL_KEY = 'dsakiko.home.recent-model.v1'
const EXCLUDED_TOOLS = ['ask_user', 'start_lottery']

/** @param {HomeTarget} target 导航目标。 @returns {string} 草稿身份，不依赖展示名称。 */
export function inputKey(target) {
  return target.chatId ? 'chat:' + target.chatId : 'new:' + target.characterId
}

/** 主页业务状态，负责命令身份和迟到结果隔离；不包含 React、模型 SDK 或声音。 */
export class HomeFlow {
  /** @param {import('../../backend/client.js').BackendClient} client 应用共享的业务连接。 @param {{storage?: Storage, randomId?: () => string}} options 导航存储和命令身份。 */
  constructor(
    client,
    { storage = globalThis.localStorage, randomId = () => crypto.randomUUID() } = {}
  ) {
    Object.assign(this, { client, storage, randomId })
    this.listeners = new Set()
    this.inputs = new Map()
    this.tabChats = new Map()
    this.epoch = 0
    this.refreshEpoch = 0
    this.mountEpoch = 0
    /** @type {HomeState} */
    this.state = {
      loading: true,
      characters: [],
      chats: [],
      target: { characterId: null, chatId: null, tab: 'single-character' },
      snapshot: null,
      settings: null,
      connections: [],
      builtinConnection: false,
      draftModel: null,
      notices: [],
      draft: '',
      draftOutfit: null,
      sending: false,
      problem: null
    }
  }
  getSnapshot = () => this.state
  subscribe = (listener) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  update(value) {
    if (this.closed) return
    this.state = { ...this.state, ...value }
    for (const listener of this.listeners) listener()
  }
  async mount() {
    // StrictMode 会清理后复用同一实例；恢复可用状态，同时隔离上次挂载未完成的查询。
    this.closed = false
    const epoch = ++this.mountEpoch
    try {
      await this.refresh()
      if (this.closed || epoch !== this.mountEpoch) return
      let target
      try {
        target = JSON.parse(this.storage?.getItem(NAVIGATION_KEY) ?? 'null')
      } catch {
        /* 损坏的导航不影响正式数据。 */
      }
      const first =
        this.state.characters.find((role) => role.id === 'sakiko') ?? this.state.characters[0]
      if (!target)
        target = { characterId: first?.id ?? null, chatId: null, tab: 'single-character' }
      if (target.chatId && !this.state.chats.some((chat) => chat.id === target.chatId))
        target.chatId = null
      if (!target.chatId && !this.state.characters.some((role) => role.id === target.characterId))
        target.characterId = first?.id ?? null
      if (!['single-character', 'scripted-dialogue'].includes(target.tab))
        target.tab = 'single-character'
      this.select(target)
      this.stops = [
        this.client.settings.observe((settings) => this.update({ settings })),
        this.client.notices.observe((snapshot) => this.update({ notices: snapshot.notices })),
        this.client.observe(
          'characters',
          null,
          () => void this.refresh().catch((error) => this.update({ problem: error.message }))
        )
      ]
      this.update({ loading: false })
    } catch (error) {
      if (epoch === this.mountEpoch) this.update({ loading: false, problem: error.message })
    }
  }
  async refresh() {
    const epoch = ++this.refreshEpoch
    const observingSettings = Boolean(this.stops)
    const [characters, chats, settings, connections, availability] = await Promise.all([
      this.client.characters.list(),
      this.client.conversations.listChats(),
      this.client.settings.get(),
      this.client.settings.listConnections(),
      this.client.settings.connectionAvailability?.()
    ])
    if (epoch === this.refreshEpoch) {
      const next = {
        characters,
        chats,
        connections: connections.filter((item) => item.id !== 'builtin-up'),
        builtinConnection:
          availability?.builtin ?? connections.some((item) => item.id === 'builtin-up')
      }
      // 后续列表刷新不以一次 HTTP 查询覆盖已在观察的设置快照。
      if (!observingSettings && !this.stops) next.settings = settings
      this.update(next)
    }
  }
  input(target = this.state.target) {
    const key = inputKey(target)
    if (!this.inputs.has(key)) {
      const model = target.chatId ? null : this.recentModel()
      this.inputs.set(key, { text: '', pending: false, intent: null, model })
    }
    return this.inputs.get(key)
  }
  select(target) {
    this.stopChat?.()
    if (target?.tab) {
      this.tabChats.set(target.tab, target.chatId ?? null)
    }
    const epoch = ++this.epoch
    const input = this.input(target)
    this.update({
      target,
      snapshot: null,
      draft: input.text,
      draftOutfit: target.chatId ? null : (input.outfit ?? null),
      draftModel: input.model ?? null,
      sending: input.pending,
      problem: null
    })
    try {
      this.storage?.setItem(NAVIGATION_KEY, JSON.stringify(target))
    } catch {
      /* 导航保存失败不阻止聊天。 */
    }
    if (!target.chatId) return
    this.stopChat = this.client.conversations.observe(target.chatId, (snapshot) => {
      if (epoch !== this.epoch) return
      this.update({ snapshot })
    })
  }
  selectChat = (chat) =>
    this.select({ characterId: chat.characterIds[0] ?? null, chatId: chat.id, tab: chat.mode })
  newChat = (characterId = this.state.target.characterId) => {
    const target = { characterId, chatId: null, tab: 'single-character' }
    const input = this.input(target)
    if (!input.pending) input.outfit = null
    this.select(target)
  }
  setTab = (tab) => {
    const rememberedId = this.tabChats.get(tab) ?? null
    const chat = rememberedId
      ? this.state.chats.find((item) => item.id === rememberedId && item.mode === tab)
      : null
    const characterId = chat
      ? (chat.characterIds[0] ?? this.state.target.characterId)
      : this.state.target.characterId
    this.select({ ...this.state.target, characterId, chatId: chat?.id ?? null, tab })
  }
  async createTheatre(characterIds, settings, title) {
    const id = await this.client.conversations.createChat({
      mode: 'scripted-dialogue',
      characterIds,
      settings,
      title
    })
    await this.refresh()
    this.select({ chatId: id, characterId: characterIds[0], tab: 'scripted-dialogue' })
  }

  async generateTheatre() {
    const { target, snapshot } = this.state
    const input = this.input()
    if (!snapshot?.admission.canGenerate || input.pending) return
    input.pending = true
    this.liveTheatre = target
    this.update({ sending: true, problem: null })
    const options = { requestId: this.randomId(), excludedTools: EXCLUDED_TOOLS }
    try {
      const turn = snapshot.chat.turns.at(-1)
      await this.client.conversations.generateScenario(target.chatId, options, turn?.id ?? null)
    } catch (error) {
      if (this.state.target === target) this.update({ problem: error.message })
    } finally {
      input.pending = false
      if (this.input() === input) this.update({ sending: false })
    }
  }

  setDraft = (text) => {
    this.input().text = text
    this.update({ draft: text })
  }
  recentModel() {
    try {
      const value = JSON.parse(this.storage?.getItem(RECENT_MODEL_KEY) ?? 'null')
      if (modelExists(this.state.connections, value)) return value
    } catch {
      /* 损坏的本地模型记录不阻塞默认选择。 */
    }
    return (
      validUserSelection(this.state.connections, this.state.settings?.generation) ??
      firstAvailableModel(this.state.connections)
    )
  }

  /** @param {ModelSelection} model 新选中的连接与模型。 @returns {Promise<void>} 成功选择才更新下次新对话的默认项。 */
  chooseModel = async (model) => {
    const target = this.state.target
    if (!modelExists(this.state.connections, model)) return
    try {
      if (target.chatId)
        await this.client.conversations.patchSettings(target.chatId, { generation: model })
      else {
        const draft = this.input(target)
        draft.model = model
        if (this.state.target === target) this.update({ draftModel: model })
      }
      try {
        this.storage?.setItem(RECENT_MODEL_KEY, JSON.stringify(model))
      } catch {
        /* 临时选择仍然可用。 */
      }
    } catch (error) {
      if (this.state.target === target) this.update({ problem: error.message })
    }
  }

  /**
   * @param {string} formId 当前角色形态。
   * @param {import('../../../../shared/contracts/characters.js').CharacterModel} model 选中的服装。
   * @returns {Promise<void>} 正式对话只覆盖当前形态的服装，草稿保留到首轮提交。
   */
  chooseOutfit = async (formId, model, actorId) => {
    const { chatId, tab } = this.state.target
    const characterId = tab === 'scripted-dialogue' ? actorId : this.state.target.characterId
    if (!characterId || (!chatId && tab !== 'single-character')) return
    if (chatId) {
      // 部分更新在后端与最新配置合并，保留其他形态的服装覆盖。
      await this.client.conversations.patchSettings(chatId, {
        characters: {
          [characterId]: {
            formId,
            modelOverrides: { [formId]: model.id }
          }
        }
      })
    } else {
      const input = this.input()
      if (input.pending) return
      input.outfit = { formId, model }
      this.update({ draftOutfit: input.outfit })
    }
  }

  /**
   * @param {string} characterId 删除服装的角色。
   * @param {string} formId 删除服装的形态。
   * @param {string} modelId 被删除的模型。
   */
  invalidateDraftOutfit(characterId, formId, modelId) {
    const draft = this.inputs.get(inputKey({ characterId, chatId: null }))
    if (draft?.outfit?.formId !== formId || draft.outfit.model.id !== modelId) return
    draft.outfit = null
    if (!this.state.target.chatId && this.state.target.characterId === characterId)
      this.update({ draftOutfit: null })
  }

  /** @param {InputSession} input 录音开始时绑定的草稿。 @param {string} text 识别结果。 */
  appendRecognition(input, text) {
    if (![...this.inputs.values()].includes(input)) return
    input.text += input.text && !/\s$/.test(input.text) ? '\n' + text : text
    if (input === this.input()) this.update({ draft: input.text })
  }
  /** @param {(chatId: string) => void} [onAccepted] 确认受理后通知舞台。 @returns {Promise<void>} 失败重试复用业务身份，迟到响应仅修改原会话。 */
  send = async (onAccepted) => {
    const target = { ...this.state.target }
    const input = this.input(target)
    if (
      input.pending ||
      !input.text.trim() ||
      target.tab !== 'single-character' ||
      !target.characterId
    )
      return
    const key = inputKey(target)
    const text = input.text
    let chatId = input.chatId ?? target.chatId
    const draftModel = chatId ? null : validUserSelection(this.state.connections, input.model)
    const model = draftModel ?? selectedModel(this.state)
    const modelKey = JSON.stringify(model)
    const outfit = chatId ? null : input.outfit
    const outfitKey = outfit ? JSON.stringify([outfit.formId, outfit.model.id]) : null
    if (
      !input.intent ||
      input.intent.text !== text ||
      input.intent.modelKey !== modelKey ||
      input.intent.outfitKey !== outfitKey
    )
      input.intent = { text, model, modelKey, outfit, outfitKey, requestId: this.randomId() }
    const intent = input.intent
    input.pending = true
    this.update({ sending: true, problem: null })
    try {
      const submission = { text, attachments: [] }
      const options = { requestId: intent.requestId, excludedTools: EXCLUDED_TOOLS }
      if (chatId) {
        const chatGeneration = this.state.snapshot?.chat?.meta?.settings?.generation
        // 仅当会话自身未指向有效自有模型时随轮次补齐兜底模型，避免覆盖对话已有设置。
        const turnOptions =
          intent.model && !modelExists(this.state.connections, chatGeneration)
            ? { ...options, generation: intent.model }
            : options
        await this.client.conversations.createTurn(chatId, submission, turnOptions)
      } else {
        const settings = {}
        if (intent.model) settings.generation = intent.model
        if (intent.outfit) {
          const { formId, model } = intent.outfit
          settings.characters = {
            [target.characterId]: { formId, modelOverrides: { [formId]: model.id } }
          }
        }
        const result = await this.client.conversations.createChatWithTurn(
          {
            title: text.trim().slice(0, 40),
            characterIds: [target.characterId],
            mode: 'single-character',
            ...(Object.keys(settings).length ? { settings } : {})
          },
          submission,
          options
        )
        chatId = result.chatId
      }
      input.chatId = chatId
      input.model = null
      input.outfit = null
      if (input.text === text) input.text = ''
      input.intent = null
      input.pending = false
      this.inputs.set('chat:' + chatId, input)
      // 新建占位草稿交给正式聊天后，下一次“新对话”必须得到新的会话身份。
      if (!target.chatId) this.inputs.delete(key)
      onAccepted?.(chatId)
      if (inputKey(this.state.target) === key) this.select({ ...target, chatId })
      await this.refresh()
    } catch (error) {
      if (inputKey(this.state.target) === key) this.update({ problem: error.message })
    } finally {
      input.pending = false
      if (this.input() === input) this.update({ sending: false, draft: input.text })
    }
  }
  stop = async () => {
    const snapshot = this.state.snapshot
    if (!snapshot?.activeTurnId) return
    try {
      await this.client.conversations.stopTurn(snapshot.chat.id, snapshot.activeTurnId)
    } catch (error) {
      this.update({ problem: error.message })
    }
  }
  deleteChat = async (chat) => {
    try {
      await this.client.conversations.deleteChat(chat.id)
      this.inputs.delete('chat:' + chat.id)
      if (this.tabChats.get(chat.mode) === chat.id) {
        this.tabChats.set(chat.mode, null)
      }
      if (this.state.target.chatId === chat.id) this.select({ ...this.state.target, chatId: null })
      await this.refresh()
      return true
    } catch (error) {
      this.update({ problem: error.message })
      return false
    }
  }
  retrySave = async () => {
    const chatId = this.state.target.chatId
    if (!chatId) return
    try {
      await this.client.conversations.retrySave(chatId)
      this.update({ problem: null })
    } catch (error) {
      this.update({ problem: error.message })
    }
  }
  dismiss = async (id) => {
    try {
      await this.client.notices.dismiss(id)
    } catch (error) {
      this.update({ problem: error.message })
    }
  }
  dispose() {
    this.closed = true
    this.mountEpoch += 1
    this.refreshEpoch += 1
    this.epoch += 1
    this.stopChat?.()
    this.stopChat = null
    this.stops?.forEach((stop) => stop())
    this.stops = null
    this.listeners.clear()
  }
}

/** @param {object} state 主页快照。 @returns {boolean} 当前选择是否指向确实存在的连接和模型；不推断凭据或网络可用性。 */
export function hasConnection(state) {
  return Boolean(state.builtinConnection || modelExists(state.connections, selectedModel(state)))
}

function modelExists(connections, pair) {
  return Boolean(
    pair &&
    connections.some(
      (item) =>
        item.id === pair.connectionId && item.models.some((model) => model.id === pair.modelId)
    )
  )
}

/**
 * 从连接目录中选取第一个包含模型的条目作为兜底默认模型。
 * @param {import('../../../../shared/contracts/models.js').ConnectionDefinition[]} connections 可用连接列表。
 * @returns {ModelSelection|null} 第一个可用连接的首个模型；无可用模型时返回 null。
 */
function firstAvailableModel(connections) {
  for (const item of connections) {
    if (item.models?.length) return { connectionId: item.id, modelId: item.models[0].id }
  }
  return null
}

/**
 * 校验模型选择是否指向当前存在的自有连接（排除内置连接）。
 * @param {import('../../../../shared/contracts/models.js').ConnectionDefinition[]} connections 可用连接列表。
 * @param {Partial<ModelSelection>|null|undefined} selection 待校验的模型配置。
 * @returns {ModelSelection|null} 合法的自有模型选择，无效时返回 null。
 */
function validUserSelection(connections, selection) {
  if (
    selection?.connectionId &&
    selection.modelId &&
    selection.connectionId !== 'builtin-up' &&
    modelExists(connections, selection)
  )
    return { connectionId: selection.connectionId, modelId: selection.modelId }
  return null
}

/** @param {HomeState} state 当前对话或待发送的草稿。 @returns {ModelSelection|null} 生效的自有模型。 */
export function selectedModel(state) {
  if (!state.target.chatId) {
    return (
      validUserSelection(state.connections, state.draftModel) ??
      validUserSelection(state.connections, state.settings?.generation) ??
      firstAvailableModel(state.connections)
    )
  }
  const chat = state.snapshot?.chat
  if (!chat) return null
  const generation = chat.meta.settings.generation
  if (chat.meta.extensions?.pythonImport) return validUserSelection(state.connections, generation)
  const selection = generation.connectionId ? generation : state.settings?.generation
  return validUserSelection(state.connections, selection) ?? firstAvailableModel(state.connections)
}
