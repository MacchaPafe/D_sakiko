import { extractAssetReferences } from '../resources/references.js'
import { selectGptSovitsFiles } from '../../shared/contracts/speech-resources.js'
import path from 'node:path'
import { VertexDrafts, vertexFields, vertexPrivateKey } from './vertex.js'
import { ChatGPTAccounts } from './chatgpt-accounts.js'
import {
  copy,
  merge,
  fields,
  requireValue,
  uuid,
  readJson,
  writeJson,
  SerialQueue,
  problem,
  SnapshotBus
} from '../core/support.js'
import {
  config,
  configPath,
  parseConfig,
  configPatch,
  applyConfigPatch,
  isConfigObject
} from '../core/config.js'
import { settingsDefinition, createDefaultSettings } from './definition.js'
import { providerPresets } from '../../shared/contracts/llm-providers.js'
import { ReasoningCatalog } from './reasoning-catalog.js'
import { normalizeConnection } from '../llm/connections.js'

const revisionItem = config.number({ integer: true, min: 0, max: Number.MAX_SAFE_INTEGER })

/**
 * @typedef {object} SettingsWriteOptions
 * @property {number} [expectedRevision] 当前所基于的设置版本号，用于防止并发修改互相覆盖。如果不传，则直接修改最新的设置。
 * @property {boolean} [correct] 是否自动修复超出范围或不合法的值，默认不修复并直接报错。
 */

/**
 * @typedef {object} SettingsState
 * @property {import('../../shared/contracts/settings.js').GlobalSettings} settings 全局设置。
 * @property {Record<string, import('../../shared/contracts/models.js').ConnectionDefinition>} connections 已配置的模型连接字典。
 * @property {string[]} [connectionOrder] 已配置连接的展示顺序。
 * @property {Record<string, string>} secrets 各连接对应的 API 密钥（仅保存在后端，不随连接列表对外返回）。
 * @property {number} [speechDefaultsVersion] 标记旧默认值已迁移，允许用户之后主动选回原数值。
 */

/**
 * 按持久化顺序过滤有效连接并补齐未登记的新连接，兼容缺少顺序字段的旧版设置。
 * @param {SettingsState} state 设置状态对象。
 * @returns {string[]} 排序后的连接 ID 列表。
 */
function orderedConnectionIds(state) {
  const allIds = Object.keys(state.connections)
  const valid = new Set(allIds)
  const seen = new Set()
  const ordered = []
  if (Array.isArray(state.connectionOrder)) {
    for (const id of state.connectionOrder) {
      if (!valid.has(id) || seen.has(id)) continue
      seen.add(id)
      ordered.push(id)
    }
  }
  for (const id of allIds) {
    if (!seen.has(id)) ordered.push(id)
  }
  return ordered
}

/**
 * 全局设置与模型连接管理模块。
 */
export class Settings {
  /**
   * 创建一个全局设置管理器。
   * @param {object} options 创建选项。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 媒体资源管理器。
   * @param {string} options.directory 设置文件（settings.json）所在的存储目录。
   * @param {((connection: import('../../shared/contracts/models.js').ResolvedModelConnection, signal?: AbortSignal) => Promise<unknown>) | null} [options.probe] 自定义函数，用于发送最小测试请求以检查模型连接是否可用。
   */
  constructor({ directory, assets, probe = null, modelCatalog = null, transport = fetch }) {
    this.file = path.join(directory, 'settings.json')
    this.queue = new SerialQueue()
    this.scopes = new Map()
    this.reasoningCatalog = new ReasoningCatalog(directory, transport)
    this.vertexDrafts = new VertexDrafts()
    this.accounts = new ChatGPTAccounts(directory)
    this.bus = new SnapshotBus()
    this.probe = probe
    this.assets = assets
    this.modelCatalog = modelCatalog
    assets.registerOwnerReader('settings', async (id) => {
      requireValue(id === 'global', 'invalid_owner')
      return this.get()
    })
  }

  /**
   * 读取包含私有密钥的完整存储状态。
   * @returns {Promise<SettingsState>} 内部设置状态对象。
   */
  async state() {
    const state = await readJson(this.file, {
      settings: createDefaultSettings(),
      connections: {},
      secrets: {}
    })
    fields(state, [
      'settings',
      'connections',
      'secrets',
      'voicePreference',
      'connectionOrder',
      'speechDefaultsVersion'
    ])
    requireValue(
      isConfigObject(state.settings) &&
        isConfigObject(state.connections) &&
        isConfigObject(state.secrets) &&
        (state.connectionOrder === undefined ||
          (Array.isArray(state.connectionOrder) &&
            state.connectionOrder.every((id) => typeof id === 'string'))),
      'invalid_settings',
      '设置文件结构无效'
    )
    const { revision, ...values } = state.settings
    if (state.speechDefaultsVersion === undefined) {
      if (values.speech?.speed === 1 && values.speech.sentencePauseMs === 300) {
        delete values.speech.speed
        delete values.speech.sentencePauseMs
      }
      state.speechDefaultsVersion = 1
    }
    if (values.generation?.compression?.thresholdTokens === 258000)
      values.generation.compression.thresholdTokens = 270000
    state.settings = {
      revision: revisionItem.parse(revision, 'settings.revision'),
      ...parseConfig(settingsDefinition, values, { defaults: true })
    }
    return state
  }

  /**
   * 订阅全局设置的变更通知（订阅时会立即推送一次当前设置）。
   * @param {(settings: import('../../shared/contracts/settings.js').GlobalSettings) => void} listener 接收最新全局设置的回调函数。
   * @returns {Promise<() => void>} 取消订阅的函数。
   */
  async observe(listener) {
    return this.bus.subscribe('settings', listener, await this.get())
  }

  /** 自动语音只适用于仍与下载意图一致的用户偏好版本。 */
  async voicePreference() {
    const state = await this.state()
    const preference = state.voicePreference ?? { revision: 0, enabled: {} }
    // 旧版本允许在全局关闭后通过下载重新登记角色，不能将这些记录当作用户重新开启。
    if (preference.revision > 0 && !state.settings.speech.enabled)
      return { revision: preference.revision, enabled: {} }
    return preference
  }

  async enablePreparedVoice(characterId, revision) {
    return this.queue.run('settings', async () => {
      const state = await this.state()
      const preference = state.voicePreference ?? { revision: 0, enabled: {} }
      if (preference.revision !== revision) return false
      if (revision > 0 && !state.settings.speech.enabled) return false
      if (preference.enabled[characterId] !== true) {
        preference.enabled[characterId] = true
        state.voicePreference = preference
        await writeJson(this.file, state)
      }
      return true
    })
  }

  /**
   * 根据临时凭据作用域 ID 获取对应的 API 密钥。
   * @param {string} scope 由 resolveConnection 分配的临时作用域 ID。
   * @returns {string | ((rejected?: string) => Promise<string>)} API 密钥、服务账号私钥或订阅令牌获取函数。
   */
  credential(scope) {
    requireValue(this.scopes.has(scope), 'credential_expired')
    return this.scopes.get(scope)
  }

  /** 保存工具厂商密钥；公开设置与查询结果不包含密钥值。 */
  async saveToolCredential(provider, credential) {
    requireValue(provider === 'bocha', 'tool_provider_unavailable')
    requireValue(['keep', 'replace', 'remove'].includes(credential?.kind), 'invalid_credential')
    return this.queue.run('settings', async () => {
      const state = await this.state()
      const key = 'tool:' + provider
      if (credential.kind === 'replace') {
        requireValue(
          typeof credential.secret === 'string' && credential.secret.trim(),
          'invalid_credential'
        )
        state.secrets[key] = credential.secret
      }
      if (credential.kind === 'remove') delete state.secrets[key]
      await writeJson(this.file, state)
      return { configured: Boolean(state.secrets[key]) }
    })
  }

  /** 仅供后端工具取得私有凭据，不能公开到 HTTP 方法表。 */
  async toolCredential(provider) {
    return (await this.state()).secrets['tool:' + provider] ?? ''
  }

  /** 查询工具配置状态，不返回凭据。 */
  async getToolCredentialStatus(provider) {
    requireValue(provider === 'bocha', 'tool_provider_unavailable')
    return { configured: Boolean(await this.toolCredential(provider)) }
  }

  /**
   * @overload
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 全局设置副本。
   */
  /**
   * @overload
   * @param {string} path 点分隔的配置项路径。
   * @returns {Promise<unknown>} 指定配置项的值副本；未设置的可选项返回 undefined。
   */
  /**
   * 获取当前的配置。如果不传入路径，将返回所有设置项目。传入路径后，只返回该路径下的设置。
   * @param {string} [path] 点分隔的配置项路径，例如 'generation.temperature'。
   * @returns {Promise<unknown>} 完整的全局设置副本，或单个配置项的值。
   */
  async get(path) {
    const keys = path === undefined ? [] : configPath(settingsDefinition, path)
    let value = (await this.state()).settings
    for (const key of keys) value = value[key]
    return copy(value)
  }

  /** 在设置锁内交接 Python 启动材料，设置更换不能提前释放旧进程所需资源。 */
  async acquireSpeechResources({ allArchitectures = false } = {}) {
    return this.queue.run('settings', async () => {
      const startup = (await this.get()).speechStartup
      const { resources, architecture } = startup.gptSovits
      let { files, missing } = selectGptSovitsFiles(resources, architecture)
      if (allArchitectures) {
        const available = Object.keys(resources.versions).filter(
          (version) => selectGptSovitsFiles(resources, version).missing.length === 0
        )
        requireValue(available.length > 0, 'missing_resources', '尚未准备可用的 GPT-SoVITS 资源。')
        files = {
          shared: resources.shared,
          versions: Object.fromEntries(
            available.map((version) => [version, resources.versions[version]])
          )
        }
        missing = []
      }
      requireValue(
        missing.length === 0,
        'missing_resources',
        '缺少 GPT-SoVITS 资源：' + missing.join('、')
      )
      const owner = await this.assets.createTemporaryOwner(
        'speech-base',
        extractAssetReferences(files)
      )
      return {
        startup: allArchitectures
          ? { ...startup, gptSovits: { ...startup.gptSovits, architecture: 'auto' } }
          : startup,
        files,
        owner
      }
    })
  }

  /**
   * 修改单个配置项目。
   * @param {string} path 点分隔的配置项路径，例如 'generation.temperature'。
   * @param {unknown} value 要设置的新值。
   * @param {SettingsWriteOptions} [options] 版本号检查与自动修复选项。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 保存后的完整全局设置副本。
   */
  async set(path, value, options = {}) {
    return this.update(configPatch(settingsDefinition, path, value), options)
  }

  /**
   * 批量修改多个配置项目。
   * @param {import('../../shared/contracts/settings.js').SettingsUpdate} patch 要更新的配置项集合。只需要输入修改的内容。
   * @param {SettingsWriteOptions} [options] 版本号检查与自动修复选项。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 保存后的完整全局设置副本。
   */
  async update(patch, options = {}) {
    fields(options, ['expectedRevision', 'correct'])
    const { expectedRevision, correct = false } = options
    requireValue(typeof correct === 'boolean', 'invalid_settings', 'correct 必须为布尔值')
    if (expectedRevision !== undefined) revisionItem.parse(expectedRevision, 'expectedRevision')
    // 在进入异步队列前先完成参数校验与深拷贝，避免调用方在排队等待期间修改传入的 patch 对象。
    const changes = parseConfig(settingsDefinition, patch, {
      partial: true,
      deepPartial: true,
      correct
    })
    return this.queue.run('settings', async () => {
      const state = await this.state()
      if (expectedRevision !== undefined)
        requireValue(expectedRevision === state.settings.revision, 'revision_conflict')
      requireValue(
        state.settings.revision < Number.MAX_SAFE_INTEGER,
        'invalid_settings',
        '设置版本号已达到上限'
      )
      const next = applyConfigPatch(settingsDefinition, state.settings, changes)
      if (Object.hasOwn(changes.speech ?? {}, 'enabled')) {
        state.voicePreference = {
          revision: (state.voicePreference?.revision ?? 0) + 1,
          enabled: {}
        }
      }
      next.revision += 1
      state.settings = next
      await this.assets.commitReferences('settings:global', next, async () => {
        for (const ref of extractAssetReferences(next.speechStartup.gptSovits.resources)) {
          const info = await this.assets.inspect(ref)
          requireValue(
            info.kind === 'file' && info.purpose === 'model',
            'invalid_resource_manifest'
          )
        }
        await writeJson(this.file, state)
      })
      this.bus.emit('settings', next)
      return copy(next)
    })
  }

  /** @param {boolean} refresh 是否更新官方目录。 @returns {Promise<object[]>} 模型与思考能力，不含凭据。 */
  async listReasoningModels(refresh = false) {
    const state = await this.state()
    const profiles = Object.values(state.connections).some((c) => c.provider === 'chatgpt')
      ? await this.accounts.list()
      : []
    const entries = await Promise.all(
      Object.values(state.connections).map(async (saved) => {
        const connection = normalizeConnection(saved)
        if (refresh) await this.reasoningCatalog.refresh(connection, state.secrets[connection.id])
        return Promise.all(
          connection.models.map(async (model) => {
            const capabilities = await this.reasoningCatalog.resolve(connection, model)
            const entry = profiles
              .find((p) => p.id === connection.accountId)
              ?.models?.find((m) => m.id === model.id)
            if (entry)
              Object.assign(capabilities, {
                reasoning: { status: entry.efforts?.length ? 'supported' : 'unknown' },
                reasoningEfforts: entry.efforts,
                reasoningToggle: entry.efforts?.includes('none')
              })
            return {
              connectionId: connection.id,
              modelId: model.id,
              provider: connection.provider,
              baseUrl: connection.baseUrl,
              protocol: connection.protocol,
              capabilities: merge(capabilities, model.overrides)
            }
          })
        )
      })
    )
    return entries.flat()
  }
  async listConnections() {
    const state = await this.state()
    const connections = orderedConnectionIds(state).map((id) => copy(state.connections[id]))
    if (connections.some((item) => item.provider === 'chatgpt')) {
      const profiles = await this.accounts.list()
      for (const item of connections)
        if (item.provider === 'chatgpt')
          item.credentialConfigured = profiles.some((p) => p.id === item.accountId && p.connected)
    }
    return connections
  }

  /** 列出可用于创建多个独立连接的厂商与协议预设。
   * @returns {import('../../shared/contracts/llm-providers.js').ProviderPreset[]} 不含凭据的预设副本。
   */
  listProviderPresets() {
    return copy(providerPresets)
  }

  /**
   * 创建或更新一个模型连接配置及其 API 密钥。
   * @param {import('../../shared/contracts/models.js').ConnectionDefinition} definition 模型连接配置。新建时 revision 为 0，更新时传入当前版本号。
   * @param {{kind:'keep'|'remove'} | {kind:'replace',secret:string} | {kind:'vertex-import',ticket:string}} credential 保留、移除、替换或从本机文件导入凭据。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition>} 保存成功并递增版本号后的连接配置副本。
   */
  async saveConnection(definition, credential) {
    definition = copy(definition)
    credential = copy(credential)
    return this.queue.run('settings', async () => {
      const state = await this.state()
      const ticket = credential?.kind === 'vertex-import' ? credential.ticket : null
      credential = this.draftCredential(definition, credential)
      definition = this.validateConnectionDraft(definition, credential)
      await this.checkAccountBinding(definition, state.connections)
      const previous = state.connections[definition.id]
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      this.checkCredentialProvider(previous, definition, credential)
      if (credential.kind === 'keep' && previous && state.secrets[definition.id])
        requireValue(
          new URL(previous.baseUrl).origin === new URL(definition.baseUrl).origin,
          'invalid_connection',
          '更改服务域名时，请重新填写或清除原 API Key。'
        )
      if (definition.provider === 'vertex')
        requireValue(
          credential.kind === 'replace' || state.secrets[definition.id],
          'invalid_credential',
          '请提供服务账号私钥。'
        )
      if (credential.kind === 'replace') {
        state.secrets[definition.id] = credential.secret
      }
      if (credential.kind === 'remove') delete state.secrets[definition.id]
      const next = {
        ...copy(definition),
        revision: definition.revision + 1,
        credentialConfigured:
          definition.provider === 'chatgpt' || Boolean(state.secrets[definition.id])
      }
      state.connections[definition.id] = next
      if (
        Array.isArray(state.connectionOrder) &&
        definition.id !== 'builtin-up' &&
        !state.connectionOrder.includes(definition.id)
      ) {
        state.connectionOrder.push(definition.id)
      }
      await writeJson(this.file, state)
      if (ticket) this.vertexDrafts.discard(ticket)
      this.bus.emit('settings', state.settings)
      return copy(next)
    })
  }

  draftCredential(definition, credential) {
    if (credential?.kind === 'vertex-import') {
      requireValue(definition.provider === 'vertex', 'invalid_credential')
      return { kind: 'replace', secret: this.vertexDrafts.get(credential.ticket) }
    }
    return credential
  }

  checkCredentialProvider(previous, definition, credential) {
    if (
      previous &&
      previous.provider !== definition.provider &&
      [previous.provider, definition.provider].some((p) => p === 'vertex' || p === 'chatgpt')
    )
      requireValue(false, 'invalid_connection', '请为不同的认证方式新建连接。')
    if (definition.provider === 'vertex' && credential.kind === 'replace')
      vertexPrivateKey(credential.secret)
    if (definition.provider === 'chatgpt')
      requireValue(credential.kind === 'keep', 'invalid_credential')
  }

  async checkAccountBinding(definition, connections) {
    if (definition.provider !== 'chatgpt') return
    requireValue(
      !Object.values(connections).some(
        (c) => c.id !== definition.id && c.accountId === definition.accountId
      ),
      'account_already_connected',
      '此注册已经有一条连接，请编辑已有连接。'
    )
    const previous = connections[definition.id]
    requireValue(
      !previous || previous.accountId === definition.accountId,
      'account_changed',
      '更换注册请新建连接。'
    )
    const account = (await this.accounts.list()).find((p) => p.id === definition.accountId)
    requireValue(account?.connected, 'login_required', '请先登录 ChatGPT 并授权使用套餐。')
    requireValue(
      definition.models.length > 0 &&
        definition.models.every((m) => account.models.some((entry) => entry.id === m.id)),
      'model_unavailable',
      '请选择该账号目录中的模型。'
    )
  }

  /** 保存和测试共用校验；编辑草稿只能从当前版本读取原密钥。 */
  validateConnectionDraft(definition, credential) {
    fields(definition, [
      'id',
      'revision',
      'name',
      'provider',
      'baseUrl',
      'protocol',
      'behavior',
      'credentialConfigured',
      'vertex',
      'accountId',
      'attachmentTransport',
      'models'
    ])
    requireValue(
      typeof definition.id === 'string' &&
        /^[a-zA-Z0-9_-]{1,100}$/.test(definition.id) &&
        !['__proto__', 'constructor', 'prototype'].includes(definition.id)
    )
    definition.attachmentTransport ??= 'auto'
    requireValue(['auto', 'inline', 'file-api'].includes(definition.attachmentTransport))
    if (definition.provider === 'vertex') {
      definition.vertex = vertexFields(definition.vertex)
      requireValue(credential.kind !== 'remove', 'invalid_credential', '请提供服务账号私钥。')
    } else requireValue(definition.vertex == null, 'invalid_connection')
    if (definition.provider === 'chatgpt')
      requireValue(typeof definition.accountId === 'string', 'invalid_connection')
    else requireValue(definition.accountId == null, 'invalid_connection')
    definition = normalizeConnection(definition)
    requireValue(
      Array.isArray(definition.models) &&
        new Set(definition.models.map((model) => model.id)).size === definition.models.length
    )
    for (const model of definition.models) {
      if (model.catalogMapping != null) {
        fields(model.catalogMapping, ['provider', 'model'])
        requireValue(
          ['provider', 'model'].every(
            (key) =>
              typeof model.catalogMapping[key] === 'string' && model.catalogMapping[key].trim()
          ),
          'invalid_connection'
        )
      }
      requireValue(typeof model.id === 'string' && model.id.trim().length > 0, 'invalid_connection')
      for (const capabilities of [model.declared, model.overrides]) {
        requireValue(isConfigObject(capabilities), 'invalid_connection')
        for (const key of ['contextTokens', 'inputTokens', 'outputTokens'])
          requireValue(
            capabilities[key] == null ||
              (Number.isSafeInteger(capabilities[key]) && capabilities[key] > 0),
            'invalid_connection'
          )
        const binding = capabilities.requiresStablePrefixForThinking
        requireValue(binding === undefined || typeof binding === 'boolean', 'invalid_connection')
      }
    }
    fields(credential, ['kind', 'secret'])
    requireValue(['keep', 'replace', 'remove'].includes(credential.kind))
    if (credential.kind === 'replace')
      requireValue(typeof credential.secret === 'string' && credential.secret.length > 0)

    return definition
  }

  async connectionAvailability() {
    const { connections, secrets } = await this.state()
    return {
      builtin:
        Boolean(secrets['builtin-up'] && connections['builtin-up']?.models.length) &&
        !Object.keys(connections).some((id) => id !== 'builtin-up')
    }
  }

  async assertUserSelection(selection) {
    const { connections } = await this.state()
    const item = connections[selection.connectionId]
    requireValue(
      selection.connectionId !== 'builtin-up' &&
        item?.models.some((model) => model.id === selection.modelId),
      'model_unavailable',
      '所选连接或模型已不可用，请重新选择。'
    )
  }

  /**
   * 删除指定的模型连接及其保存的密钥。
   * @param {string} connectionId 要删除的连接 ID。
   * @param {number} expectedRevision 当前所基于的版本号，用于防止误删已被更新的配置。
   * @returns {Promise<void>}
   */
  async deleteConnection(connectionId, expectedRevision) {
    return this.queue.run('settings', async () => {
      const state = await this.state()
      requireValue(
        state.connections[connectionId]?.revision === expectedRevision,
        'revision_conflict'
      )
      delete state.connections[connectionId]
      delete state.secrets[connectionId]
      if (Array.isArray(state.connectionOrder)) {
        state.connectionOrder = state.connectionOrder.filter((id) => id !== connectionId)
      }
      await writeJson(this.file, state)
      this.bus.emit('settings', state.settings)
    })
  }

  /**
   * 更新已配置自有连接的展示顺序。
   * @param {string[]} connectionIds 完整且无重复的自有连接 ID 列表。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition[]>} 重排后的连接列表。
   */
  async reorderConnections(connectionIds) {
    return this.queue.run('settings', async () => {
      const state = await this.state()
      const expectedIds = Object.keys(state.connections).filter((id) => id !== 'builtin-up')
      requireValue(
        Array.isArray(connectionIds) &&
          connectionIds.length === expectedIds.length &&
          connectionIds.every(
            (id) =>
              typeof id === 'string' && id !== 'builtin-up' && Object.hasOwn(state.connections, id)
          ) &&
          new Set(connectionIds).size === connectionIds.length,
        'invalid_connection',
        '连接排序列表无效。'
      )
      state.connectionOrder = copy(connectionIds)
      await writeJson(this.file, state)
      this.bus.emit('settings', state.settings)
      return connectionIds.map((id) => copy(state.connections[id]))
    })
  }

  /**
   * 将连接 ID 和模型 ID 解析为单次请求可直接使用的连接与模型能力快照。
   * @param {{ connectionId: string, modelId: string }} selection 目标连接 ID 与模型 ID。
   * @returns {Promise<import('../../shared/contracts/models.js').ResolvedModelConnection>} 解析后的模型连接快照。
   */
  async resolveConnection(selection, { allowBuiltin = false } = {}) {
    const { connections, secrets } = await this.state()
    const hasUserConnection = Object.keys(connections).some((id) => id !== 'builtin-up')
    if (
      allowBuiltin &&
      !hasUserConnection &&
      secrets['builtin-up'] &&
      connections['builtin-up']?.models.length
    )
      selection = { connectionId: 'builtin-up', modelId: connections['builtin-up'].models[0].id }
    requireValue(
      selection.connectionId !== 'builtin-up' || (allowBuiltin && !hasUserConnection),
      'model_unavailable',
      '请为当前对话选择大模型。'
    )
    const saved = connections[selection.connectionId]
    requireValue(saved, 'model_unavailable', '请为当前对话选择大模型。')
    return this.resolveDefinition(saved, selection.modelId, secrets[saved.id])
  }

  async resolveDefinition(saved, modelId, secret) {
    const connection = normalizeConnection(saved)
    const model = connection.models.find((item) => item.id === modelId)
    requireValue(model, 'model_unavailable', '模型连接或模型不存在。')
    const catalog =
      connection.provider === 'chatgpt'
        ? {}
        : ((await this.modelCatalog?.lookup(connection, model)) ?? {})
    const capabilities = merge(catalog, await this.reasoningCatalog.resolve(connection, model))
    // 空的旧声明不应遮住目录中已知的数值；明确的正整数覆盖则始终优先。
    for (const key of ['contextTokens', 'inputTokens', 'outputTokens'])
      capabilities[key] = model.overrides[key] ?? model.declared[key] ?? catalog[key] ?? null
    if (model.overrides.contextTokens != null) capabilities.contextSource = 'manual-override'
    else if (model.declared.contextTokens != null)
      capabilities.contextSource = model.declared.contextSource ?? 'declared'
    else capabilities.contextSource = catalog.contextSource ?? null
    // 为什么分配临时的 credentialScope 而不是直接返回密钥或按 connectionId 实时查密钥：
    // 一方面避免密钥明文出现在请求配置对象中，另一方面将本次请求使用的密钥冻结在独立作用域内，防止请求进行中用户修改连接密钥导致前后不一致。
    const credentialScope = uuid()
    if (saved.provider === 'chatgpt') {
      const account = (await this.accounts.list()).find((p) => p.id === saved.accountId)
      requireValue(
        account?.connected,
        'login_required',
        '该连接需要重新登录 ChatGPT，也可以为聊天切换连接。'
      )
      requireValue(
        account.models.some((m) => m.id === modelId),
        'model_unavailable',
        '该账号的模型目录已更新，请重新选择模型。'
      )
      const entry = account.models.find((m) => m.id === modelId)
      capabilities.reasoningEfforts = entry.efforts
      capabilities.reasoning = {
        status: entry.efforts?.length ? 'supported' : 'unknown',
        source: 'chatgpt'
      }
      Object.assign(capabilities, merge(capabilities, model.overrides))
      if (entry.contextTokens) {
        capabilities.contextTokens = entry.contextTokens
        capabilities.contextSource = 'chatgpt'
      }
      if (typeof entry.image === 'boolean')
        capabilities.imageInput = {
          status: entry.image ? 'supported' : 'unsupported',
          source: 'chatgpt'
        }
      this.scopes.set(credentialScope, (rejected) =>
        this.accounts.access(saved.accountId, rejected)
      )
    } else this.scopes.set(credentialScope, secret ?? '')
    return {
      connectionId: connection.id,
      revision: connection.revision,
      modelId: model.id,
      provider: connection.provider,
      baseUrl: connection.baseUrl,
      protocol: connection.protocol,
      behavior: connection.behavior,
      ...(saved.vertex ? { vertex: saved.vertex } : {}),
      credentialScope,
      attachmentTransport: connection.attachmentTransport,
      capabilities
    }
  }

  /**
   * 发送测试请求以检查指定的模型连接是否可正常连通。
   * @param {{ connectionId: string, modelId: string }} selection 要检查的连接 ID 与模型 ID。
   * @param {AbortSignal} [signal] 控制本次检查的取消信号。
   * @returns {Promise<{ reachable: boolean, problems: import('../../shared/contracts/common.js').Problem[] }>} 连通性检查结果及错误信息。
   */
  async checkConnection(selection, signal) {
    return this.runConnectionProbe(() => this.resolveConnection(selection), signal)
  }

  /** 用临时凭据测试草稿，结束后立即释放作用域。 */
  async checkConnectionDraft(definition, credential, modelId, signal) {
    return this.runConnectionProbe(async () => {
      credential = this.draftCredential(definition, credential)
      definition = this.validateConnectionDraft(copy(definition), credential)
      requireValue(definition.id !== 'builtin-up', 'invalid_connection')
      const { connections, secrets } = await this.state()
      const previous = connections[definition.id]
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      this.checkCredentialProvider(previous, definition, credential)
      await this.checkAccountBinding(definition, connections)
      const secret = credential.kind === 'replace' ? credential.secret : secrets[definition.id]
      if (definition.provider === 'vertex') vertexPrivateKey(secret)
      if (credential.kind === 'keep' && secret && previous)
        requireValue(
          new URL(previous.baseUrl).origin === new URL(definition.baseUrl).origin,
          'invalid_connection',
          '更改服务域名时，请重新填写 API Key。'
        )
      return this.resolveDefinition(definition, modelId, credential.kind === 'remove' ? '' : secret)
    }, signal)
  }

  async runConnectionProbe(resolve, signal) {
    let connection
    try {
      requireValue(this.probe, 'adapter_unavailable', '未装配连接探测能力。')
      connection = await resolve()
      await this.probe(connection, signal)
      return { reachable: true, problems: [] }
    } catch (error) {
      if (connection && !error.problem) {
        let message = '请求测试模型失败，请核对服务地址、模型 ID 和网络。'
        if ([401, 403].includes(error.statusCode)) message = '认证失败，请检查 API Key 和模型权限。'
        if (error.statusCode === 429) message = '服务暂时限流，请稍后重试。'
        return {
          reachable: false,
          problems: [{ code: 'connection_test_failed', message, retryable: true }]
        }
      }
      return { reachable: false, problems: [problem(error)] }
    } finally {
      if (connection) this.scopes.delete(connection.credentialScope)
    }
  }
}
