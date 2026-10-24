/**
 * @typedef {object} ResourceClient
 * @property {(asset: import('../../../shared/contracts/common.js').AssetRef) => Promise<import('../../../backend/resources/interface.js').AssetInfo>} inspect 查询指定资源的元数据信息。
 * @property {(source: {name: string, mediaType: string, base64: string, purpose: import('../../../shared/contracts/assets.js').AssetPurpose, draftId?: string}) => Promise<import('../../../backend/resources/interface.js').AssetInfo>} upload 上传并导入草稿附件或长期模型资源。
 * @property {(asset: import('../../../shared/contracts/common.js').AssetRef) => Promise<{url: string, expiresAt: string, leaseId: string}>} access 获取资源的临时访问 URL 与租约 ID（客户端存活期间会自动续期）。
 * @property {(leaseId: string) => Promise<void>} releaseAccess 释放指定的资源访问租约。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['createDraft']} createDraft 创建一个新的资源草稿。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['renewDraft']} renewDraft 延长指定资源草稿的有效期。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['discardDraft']} discardDraft 放弃指定的资源草稿并释放其资源引用。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['removeDraftAssets']} removeDraftAssets 从草稿中移除指定的附件引用。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['commitDraft']} commitDraft 在目标业务数据保存后，提交草稿并完成资源引用交接。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['removeLibraryItem']} removeLibraryItem 从长期资源库中移除指定的条目。
 * @property {import('../../../backend/resources/imports.js').ResourceImports['listLibraryItems']} listLibraryItems 列出长期资源库中的所有条目。
 */
import { API_METHODS } from '../../../shared/contracts/api.js'

/** @typedef {import('../../../shared/contracts/api.js').BackendConnection} BackendConnection */
/**
 * @typedef {object} BackendClient
 * @property {Pick<import('../../../backend/conversation/interface.js').Conversations, 'listChats' | 'getSnapshot' | 'getReplayMaterials' | 'inspectContext' | 'listReminders' | 'getReminder' | 'createChat' | 'createTurn' | 'generateScenario' | 'stopTurn' | 'recreateTurn' | 'editAndRecreateTurn' | 'editHistory' | 'truncateFromTurn' | 'rollback' | 'forkChat' | 'regenerateAudio' | 'compressHistory' | 'retrySave' | 'renameChat' | 'setChatOrder' | 'setScenario' | 'setTools' | 'setWorldbookInfo' | 'setCharacterSelection' | 'setGenerationOptions' | 'setSpeechOptions' | 'setPresentation' | 'removeReminder' | 'deleteChat' | 'exportChats' | 'importChats'> & {observe: (chatId: string, listener: (snapshot: import('../../../shared/contracts/conversation.js').ConversationSnapshot) => void) => () => void}} conversations 仅包含公开业务入口；观察回调保留在客户端。
 * @property {Pick<import('../../../backend/settings/interface.js').Settings, 'get' | 'set' | 'update' | 'listConnections' | 'saveConnection' | 'deleteConnection'> & {observe: (listener: (snapshot: import('../../../shared/contracts/settings.js').GlobalSettings) => void) => () => void, checkConnection: (selection: {connectionId: string, modelId: string}, signal?: AbortSignal) => ReturnType<import('../../../backend/settings/interface.js').Settings['checkConnection']>}} settings 仅包含公开业务入口；观察回调保留在客户端。
 * @property {Pick<import('../../../backend/characters/interface.js').CharacterCatalog, 'list' | 'get' | 'save' | 'delete' | 'resolveCapabilities' | 'setOrder' | 'deletePersona' | 'listPersonas' | 'getPersona' | 'savePersona' | 'resolvePersona'>} characters 仅包含公开业务入口；观察回调保留在客户端。
 * @property {Pick<import('../../../backend/tools/interface.js').ToolCatalog, 'list'>} tools 仅包含公开业务入口；观察回调保留在客户端。
 * @property {Pick<import('../../../backend/tools/interface.js').Interactions, 'list' | 'complete'> & {observe: (listener: (snapshot: import('../../../backend/tools/interface.js').InteractionSnapshot) => void) => () => void}} interactions 仅包含公开业务入口；观察回调保留在客户端。
 * @property {Pick<import('../../../backend/performance/interface.js').Performance, 'getProgress' | 'setModelState'> & {observe: (sequenceId: string, listener: (update: import('../../../shared/contracts/presentation.js').PerformanceUpdate) => void) => () => void}} performance 仅包含公开业务入口；观察回调保留在客户端。
 * @property {ResourceClient} assets 草稿导入与可续期的资源访问。
 * @property {(namespace: string, method: string, args?: unknown[], options?: import('../../../shared/contracts/api.js').CallOptions) => Promise<unknown>} call 需要显式传输重试身份或取消等待时使用。
 * @property {(topic: string, key: string | null, listener: (snapshot: object) => void) => () => void} observe 订阅完整快照。
 * @property {(listener: (state: ConnectionState) => void) => () => void} observeConnection 观察连接状态。
 * @property {() => void} close 关闭本地连接，不停止服务。
 */

/** @typedef {{connected: boolean, problem: import('../../../shared/contracts/common.js').Problem | null}} ConnectionState 客户端传输状态，不影响后端 Turn 生命周期。 */
/**
 * @typedef {object} ClientSubscription
 * @property {string} topic 后端允许的订阅主题。
 * @property {string | null} key 主题内的对象身份，设置等全局主题为 null。
 * @property {(snapshot: object) => void} listener 完整快照接收者。
 * @property {number} revision 最近接受的观察版本。
 * @property {string | null} runtime 后端运行身份；跨运行不比较旧 revision。
 */
/**
 * @typedef {object} ConversationStore
 * @property {() => import('../../../shared/contracts/conversation.js').ConversationSnapshot | null} getSnapshot 同一快照重复读取保持对象身份，初始未连接为 null。
 * @property {(listener: () => void) => () => void} subscribe 注册变化通知，返回取消函数。
 * @property {() => void} dispose 组件卸载或切换 Chat 时释放远程订阅。
 */

/** 包装可公开问题，保留稳定 code 供未来页面处理。 */
export class BackendError extends Error {
  /**
   * 保留服务端可公开的错误码，页面无需解析错误文本。
   * @param {import('../../../shared/contracts/common.js').Problem} problem 可公开问题。
   */
  constructor(problem) {
    super(problem.message)
    this.name = 'BackendError'
    this.problem = problem
    this.code = problem.code
  }
}

/**
 * 构建 Electron/WebUI 共用的业务客户端；没有 React 或 Electron 依赖。
 * 普通方法直接 await client.conversations.createTurn(...)；取消等待不会撤销已受理命令。
 * @param {BackendConnection & { fetch?: typeof fetch, WebSocket?: typeof WebSocket, randomId?: () => string }} options 可信 bootstrap 或 WebUI 登录结果。
 * @returns {BackendClient} 分模块 Promise 方法、快照订阅及 close；call 可显式传入重试 requestId/signal。
 */
export function createBackendClient({
  baseUrl,
  token,
  fetch: fetcher = globalThis.fetch,
  WebSocket: Socket = globalThis.WebSocket,
  randomId = () => globalThis.crypto.randomUUID()
}) {
  /** @type {Map<string, ClientSubscription>} */
  const subscriptions = new Map()
  const connectionListeners = new Set()
  const accessLeases = new Set()
  const resourceDrafts = new Set()
  let leaseTimer = null
  let socket = null
  let connecting = null
  let retryTimer = null
  let closed = false
  let reconnectAttempt = 0
  let connection = { connected: false, problem: null }
  function connectionChanged(value) {
    connection = value
    for (const listener of connectionListeners) {
      try {
        listener(value)
      } catch {
        /* 界面监听异常不关闭传输。 */
      }
    }
  }
  /** 一次调用对应一次发送，不隐式重试写命令；结果不确定时由调用方复用 requestId。 */
  async function post(route, body, signal) {
    if (closed && !route.endsWith('/releaseAccess') && !route.endsWith('/discardDraft'))
      throw new BackendError({ code: 'client_closed', message: '客户端已关闭。', retryable: false })
    const response = await fetcher(baseUrl + route, {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal
    })
    const result = await response.json()
    if (result.problem) throw new BackendError(result.problem)
    if (!response.ok)
      throw new BackendError({
        code: 'transport_error',
        message: '后端请求失败。',
        retryable: true
      })
    return result.value
  }
  /**
   * 调用白名单业务方法；AbortSignal 只取消客户端等待。
   * @param {string} namespace 业务模块名称。
   * @param {string} method 公开方法名称。
   * @param {unknown[]} [args] JSON 参数，不含回调或本机路径。
   * @param {import('../../../shared/contracts/api.js').CallOptions} [options] 同一次命令重试须复用 requestId。
   * @returns {Promise<unknown>} 后端业务结果；业务失败抛 BackendError。
   */
  async function call(namespace, method, args = [], options = {}) {
    if (!Object.hasOwn(API_METHODS, namespace) || !Object.hasOwn(API_METHODS[namespace], method))
      throw new BackendError({
        code: 'method_not_found',
        message: '接口不存在。',
        retryable: false
      })
    const requestId = options.requestId ?? randomId()
    return post('/api/v1/call/' + namespace + '/' + method, { args, requestId }, options.signal)
  }
  function subscribeMessage(id, entry) {
    return { kind: 'subscribe', id, topic: entry.topic, key: entry.key }
  }
  /** 只重连快照订阅，不重放已经发送的业务命令。 */
  function retry(error) {
    if (closed || !subscriptions.size || retryTimer) return
    connectionChanged({
      connected: false,
      problem: {
        code: 'disconnected',
        message: error?.message ?? '连接已断开，正在重连。',
        retryable: true
      }
    })
    const delay = Math.min(10000, 250 * 2 ** reconnectAttempt)
    reconnectAttempt += 1
    retryTimer = setTimeout(() => {
      retryTimer = null
      connect().catch(retry)
    }, delay)
  }
  /** 并发订阅共享握手；每次重连都重新申请一次性票据。 */
  async function connect() {
    if (closed || !subscriptions.size || socket?.readyState === 1) return
    if (connecting) return connecting
    connecting = (async () => {
      const ticket = await post('/api/v1/events-ticket', {})
      if (closed || !subscriptions.size) return
      const current = new Socket(ticket.url)
      socket = current
      current.addEventListener('open', () => {
        if (closed || socket !== current) {
          current.close()
          return
        }
        reconnectAttempt = 0
        connectionChanged({ connected: true, problem: null })
        // 后端 observe 首次交付完整快照，重连无须逐条补放断线期间的事件。
        for (const [id, entry] of subscriptions)
          current.send(JSON.stringify(subscribeMessage(id, entry)))
      })
      current.addEventListener('message', (event) => {
        if (socket !== current) return
        let message
        try {
          message = JSON.parse(event.data)
        } catch {
          return
        }
        if (message.kind === 'problem') {
          connectionChanged({ connected: true, problem: message.problem })
          return
        }
        const entry = subscriptions.get(message.id)
        if (message.kind !== 'snapshot' || !entry) return
        const snapshot = message.snapshot
        const progress = snapshot.snapshot ?? snapshot
        const version = progress.revision ?? 0
        const runtime = progress.runtimeId ?? ''
        // 后端重启后 revision 可以从零开始，必须先比较运行身份再排除迟到快照。
        if (entry.runtime === runtime && entry.revision > version) return
        entry.runtime = runtime
        entry.revision = version
        try {
          entry.listener(snapshot)
        } catch {
          /* 订阅者异常不得中断其他页面的更新。 */
        }
      })
      current.addEventListener('close', () => {
        if (socket === current) {
          socket = null
          retry()
        }
      })
      current.addEventListener('error', () => current.close())
    })().finally(() => {
      connecting = null
    })
    return connecting
  }
  /**
   * 登记本地订阅，首次连接或重连后接收完整快照。
   * @param {string} topic 白名单主题。
   * @param {string | null} key 主题内身份。
   * @param {(snapshot: object) => void} listener 快照接收者。
   * @returns {() => void} 释放当前订阅，不撤销后端任务。
   */
  function observe(topic, key, listener) {
    const id = randomId()
    const entry = { topic, key, listener, revision: -1, runtime: null }
    subscriptions.set(id, entry)
    if (socket?.readyState === 1) socket.send(JSON.stringify(subscribeMessage(id, entry)))
    else connect().catch(retry)
    return () => {
      subscriptions.delete(id)
      if (socket?.readyState === 1) socket.send(JSON.stringify({ kind: 'unsubscribe', id }))
      if (!subscriptions.size) {
        clearTimeout(retryTimer)
        retryTimer = null
        socket?.close()
      }
    }
  }
  const client = {
    call,
    observe,
    /**
     * 观察传输连接状态，供页面展示断线与重连提示。
     * @param {(state: ConnectionState) => void} listener 状态接收者，登记时立即调用。
     * @returns {() => void} 取消观察。
     */
    observeConnection(listener) {
      connectionListeners.add(listener)
      listener(connection)
      return () => connectionListeners.delete(listener)
    },
    /** 释放此客户端的订阅与连接，后端仍按自身生命周期运行。
     * @returns {void} 不再自动重连。
     */
    close() {
      clearInterval(leaseTimer)
      for (const id of accessLeases) void call('assets', 'releaseAccess', [id]).catch(() => {})
      for (const id of resourceDrafts) void call('assets', 'discardDraft', [id]).catch(() => {})
      accessLeases.clear()
      resourceDrafts.clear()
      closed = true
      clearTimeout(retryTimer)
      subscriptions.clear()
      socket?.close()
      connectionListeners.clear()
    }
  }
  // 服务端与客户端共用方法表；这里只生成传输包装，不导入后端实现或向页面暴露内部方法。
  for (const [namespace, methods] of Object.entries(API_METHODS)) {
    client[namespace] = {}
    for (const method of Object.keys(methods))
      client[namespace][method] = (...args) => call(namespace, method, args)
  }
  /**
   * 启动后台定时器，定期为当前客户端持有的资源访问租约和未提交草稿自动续期。
   */
  function ensureLeaseTimer() {
    if (leaseTimer) return
    leaseTimer = setInterval(() => {
      for (const id of accessLeases) void call('assets', 'renewAccess', [id]).catch(() => {})
      for (const id of resourceDrafts) void call('assets', 'renewDraft', [id]).catch(() => {})
    }, 60000)
    leaseTimer.unref?.()
  }
  client.assets.access = async (asset) => {
    const access = await call('assets', 'access', [asset])
    if (closed) {
      await call('assets', 'releaseAccess', [access.leaseId]).catch(() => {})
      throw new BackendError({ code: 'client_closed', message: '客户端已关闭。', retryable: false })
    }
    accessLeases.add(access.leaseId)
    ensureLeaseTimer()
    return access
  }
  client.assets.releaseAccess = async (id) => {
    accessLeases.delete(id)
    return call('assets', 'releaseAccess', [id])
  }
  client.assets.createDraft = async () => {
    const draft = await call('assets', 'createDraft')
    if (closed) {
      await call('assets', 'discardDraft', [draft.draftId]).catch(() => {})
      throw new BackendError({ code: 'client_closed', message: '客户端已关闭。', retryable: false })
    }
    if (!closed) {
      resourceDrafts.add(draft.draftId)
      ensureLeaseTimer()
    }
    return draft
  }
  client.assets.discardDraft = async (id) => {
    resourceDrafts.delete(id)
    return call('assets', 'discardDraft', [id])
  }
  client.assets.commitDraft = async (id, target) => {
    const result = await call('assets', 'commitDraft', [id, target])
    if (result.released) resourceDrafts.delete(id)
    return result
  }
  client.conversations.observe = (chatId, listener) => observe('conversation', chatId, listener)
  client.performance.observe = (sequenceId, listener) =>
    observe('performance', sequenceId, listener)
  client.settings.checkConnection = (selection, signal) =>
    call('settings', 'checkConnection', [selection], { signal })
  client.settings.observe = (listener) => observe('settings', null, listener)
  client.interactions.observe = (listener) => observe('interactions', null, listener)
  return /** @type {BackendClient} */ (client)
}

/**
 * 创建可直接交给 React useSyncExternalStore 的快照容器；读取函数不发网络请求。
 * @param {BackendClient} client 已创建的业务客户端。
 * @param {string} chatId 对话身份。
 * @returns {ConversationStore} 组件卸载或切换对话时调用 dispose。
 */
export function watchConversation(client, chatId) {
  // useSyncExternalStore 要求无变更时返回同一对象；不能在 getSnapshot 中克隆或发网络请求。
  let snapshot = null
  const listeners = new Set()
  const stop = client.conversations.observe(chatId, (value) => {
    snapshot = value
    for (const listener of listeners) listener()
  })
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    dispose() {
      stop()
      listeners.clear()
    }
  }
}
