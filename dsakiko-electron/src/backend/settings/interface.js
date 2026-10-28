import { extractAssetReferences } from '../resources/references.js'
import { selectGptSovitsFiles } from '../../shared/contracts/speech-resources.js'
import path from 'node:path'
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
 * @property {Record<string, string>} secrets 各连接对应的 API 密钥（仅保存在后端，不随连接列表对外返回）。
 */

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
  constructor({ directory, assets, probe = null }) {
    this.file = path.join(directory, 'settings.json')
    this.queue = new SerialQueue()
    this.scopes = new Map()
    this.bus = new SnapshotBus()
    this.probe = probe
    this.assets = assets
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
    fields(state, ['settings', 'connections', 'secrets'])
    requireValue(
      isConfigObject(state.settings) &&
        isConfigObject(state.connections) &&
        isConfigObject(state.secrets),
      'invalid_settings',
      '设置文件结构无效'
    )
    const { revision, ...values } = state.settings
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

  /**
   * 根据临时凭据作用域 ID 获取对应的 API 密钥。
   * @param {string} scope 由 resolveConnection 分配的临时作用域 ID。
   * @returns {string} 对应的 API 密钥字符串（未配置密钥时为空字符串）。
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
  async acquireSpeechResources() {
    return this.queue.run('settings', async () => {
      const startup = (await this.get()).speechStartup
      const { resources, architecture } = startup.gptSovits
      const { files, missing } = selectGptSovitsFiles(resources, architecture)
      requireValue(
        missing.length === 0,
        'missing_resources',
        '缺少 GPT-SoVITS 资源：' + missing.join('、')
      )
      const owner = await this.assets.createTemporaryOwner(
        'speech-base',
        extractAssetReferences(files)
      )
      return { startup, files, owner }
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

  /**
   * 列出所有已配置的模型连接定义（包含是否已配置密钥的标记，但不包含密钥明文）。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition[]>} 模型连接定义列表。
   */
  async listConnections() {
    return Object.values((await this.state()).connections).map(copy)
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
   * @param {{ kind: 'keep' } | { kind: 'replace', secret: string } | { kind: 'remove' }} credential 密钥操作指令：keep 保持原密钥不变，replace 替换为新密钥，remove 清除密钥。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition>} 保存成功并递增版本号后的连接配置副本。
   */
  async saveConnection(definition, credential) {
    definition = copy(definition)
    credential = copy(credential)
    return this.queue.run('settings', async () => {
      const state = await this.state()
      fields(definition, [
        'id',
        'revision',
        'name',
        'provider',
        'baseUrl',
        'protocol',
        'behavior',
        'credentialConfigured',
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
      const previous = state.connections[definition.id]
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      definition = normalizeConnection(definition)
      requireValue(
        Array.isArray(definition.models) &&
          new Set(definition.models.map((model) => model.id)).size === definition.models.length
      )
      for (const model of definition.models) {
        requireValue(
          typeof model.id === 'string' && model.id.trim().length > 0,
          'invalid_connection'
        )
        for (const capabilities of [model.declared, model.overrides]) {
          requireValue(isConfigObject(capabilities), 'invalid_connection')
          const binding = capabilities.requiresStablePrefixForThinking
          requireValue(binding === undefined || typeof binding === 'boolean', 'invalid_connection')
        }
      }
      requireValue(['keep', 'replace', 'remove'].includes(credential.kind))
      if (credential.kind === 'replace') {
        requireValue(typeof credential.secret === 'string' && credential.secret.length > 0)
        state.secrets[definition.id] = credential.secret
      }
      if (credential.kind === 'remove') delete state.secrets[definition.id]
      const next = {
        ...copy(definition),
        revision: definition.revision + 1,
        credentialConfigured: Boolean(state.secrets[definition.id])
      }
      state.connections[definition.id] = next
      await writeJson(this.file, state)
      return copy(next)
    })
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
      await writeJson(this.file, state)
    })
  }

  /**
   * 将连接 ID 和模型 ID 解析为单次请求可直接使用的连接与模型能力快照。
   * @param {{ connectionId: string, modelId: string }} selection 目标连接 ID 与模型 ID。
   * @returns {Promise<import('../../shared/contracts/models.js').ResolvedModelConnection>} 解析后的模型连接快照。
   */
  async resolveConnection(selection) {
    const state = await this.state()
    const saved = state.connections[selection.connectionId]
    requireValue(saved, 'model_unavailable', '模型连接不存在。')
    const connection = normalizeConnection(saved)
    const model = connection?.models.find((item) => item.id === selection.modelId)
    requireValue(model, 'model_unavailable', '模型连接或模型不存在。')
    // 为什么分配临时的 credentialScope 而不是直接返回密钥或按 connectionId 实时查密钥：
    // 一方面避免密钥明文出现在请求配置对象中，另一方面将本次请求使用的密钥冻结在独立作用域内，防止请求进行中用户修改连接密钥导致前后不一致。
    const credentialScope = uuid()
    this.scopes.set(credentialScope, state.secrets[connection.id] ?? '')
    return {
      connectionId: connection.id,
      revision: connection.revision,
      modelId: model.id,
      provider: connection.provider,
      baseUrl: connection.baseUrl,
      protocol: connection.protocol,
      behavior: connection.behavior,
      credentialScope,
      attachmentTransport: connection.attachmentTransport,
      capabilities: merge(model.declared, model.overrides)
    }
  }

  /**
   * 发送测试请求以检查指定的模型连接是否可正常连通。
   * @param {{ connectionId: string, modelId: string }} selection 要检查的连接 ID 与模型 ID。
   * @param {AbortSignal} [signal] 控制本次检查的取消信号。
   * @returns {Promise<{ reachable: boolean, problems: import('../../shared/contracts/common.js').Problem[] }>} 连通性检查结果及错误信息。
   */
  async checkConnection(selection, signal) {
    try {
      requireValue(this.probe, 'adapter_unavailable', '未装配连接探测能力。')
      await this.probe(await this.resolveConnection(selection), signal)
      return { reachable: true, problems: [] }
    } catch (error) {
      return { reachable: false, problems: [problem(error)] }
    }
  }
}
