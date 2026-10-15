import { generationOptions, speechOptions } from '../core/validation.js'
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
import { defaultSettings } from '../core/defaults.js'

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
   * @param {string} options.directory 设置文件（settings.json）所在的存储目录。
   * @param {((connection: import('../../shared/contracts/models.js').ResolvedModelConnection, signal?: AbortSignal) => Promise<unknown>) | null} [options.probe] 自定义函数，用于发送最小测试请求以检查模型连接是否可用。
   */
  constructor({ directory, probe = null }) {
    this.file = path.join(directory, 'settings.json')
    this.queue = new SerialQueue()
    this.scopes = new Map()
    this.bus = new SnapshotBus()
    this.probe = probe
  }

  /**
   * 读取包含私有密钥的完整存储状态。
   * @returns {Promise<SettingsState>} 内部设置状态对象。
   */
  async state() {
    const state = await readJson(this.file, {
      settings: copy(defaultSettings),
      connections: {},
      secrets: {}
    })
    // 为什么在读取时删除旧版的 playback 和 bgm.volume 字段：
    // 播放音量已改为由各前端客户端独立保存，清理旧字段可避免保存设置时因包含未知字段而校验失败。
    delete state.settings.playback
    if (state.settings.bgm) delete state.settings.bgm.volume
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

  /**
   * 获取当前的全局设置。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 全局设置副本。
   */
  async get() {
    return copy((await this.state()).settings)
  }

  /**
   * 保存并更新全局设置。
   * @param {import('../../shared/contracts/settings.js').GlobalSettings} settings 完整的全局设置对象（需携带当前所基于的 revision 版本号）。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 保存成功并递增版本号后的全局设置副本。
   */
  async save(settings) {
    return this.queue.run('settings', async () => {
      const state = await this.state()
      fields(settings, Object.keys(copy(defaultSettings)))
      requireValue(settings.revision === state.settings.revision, 'revision_conflict')
      fields(settings.bgm, ['audio', 'enabled', 'loop'])
      generationOptions(settings.generation)
      speechOptions(settings.speech)
      fields(settings.speechStartup, [
        'device',
        'precision',
        'architecture',
        'maxConcurrentInferences',
        'maxResidentModels'
      ])
      requireValue(
        ['cpu', 'cuda', 'mps'].includes(settings.speechStartup.device) &&
          ['float16', 'float32'].includes(settings.speechStartup.precision)
      )
      requireValue(
        Number.isInteger(settings.speechStartup.maxConcurrentInferences) &&
          settings.speechStartup.maxConcurrentInferences > 0 &&
          Number.isInteger(settings.speechStartup.maxResidentModels) &&
          settings.speechStartup.maxResidentModels > 0
      )
      fields(settings.display, ['frameRate', 'theme', 'startInPetMode'])
      requireValue(
        Number.isFinite(settings.display.frameRate) &&
          settings.display.frameRate > 0 &&
          ['system', 'light', 'dark'].includes(settings.display.theme) &&
          typeof settings.display.startInPetMode === 'boolean'
      )
      requireValue(
        typeof settings.bgm.enabled === 'boolean' && typeof settings.bgm.loop === 'boolean'
      )
      const next = copy(settings)
      requireValue(next.generation && next.speech && next.speechStartup && next.display)
      next.revision += 1
      state.settings = next
      await writeJson(this.file, state)
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

  /**
   * 创建或更新一个模型连接配置及其 API 密钥。
   * @param {import('../../shared/contracts/models.js').ConnectionDefinition} definition 模型连接配置。新建时 revision 为 0，更新时传入当前版本号。
   * @param {{ kind: 'keep' } | { kind: 'replace', secret: string } | { kind: 'remove' }} credential 密钥操作指令：keep 保持原密钥不变，replace 替换为新密钥，remove 清除密钥。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition>} 保存成功并递增版本号后的连接配置副本。
   */
  async saveConnection(definition, credential) {
    return this.queue.run('settings', async () => {
      const state = await this.state()
      fields(definition, [
        'id',
        'revision',
        'name',
        'provider',
        'baseUrl',
        'protocol',
        'credentialConfigured',
        'attachmentTransport',
        'models'
      ])
      requireValue(
        typeof definition.id === 'string' &&
          /^[a-zA-Z0-9_-]{1,100}$/.test(definition.id) &&
          !['__proto__', 'constructor', 'prototype'].includes(definition.id)
      )
      requireValue(['inline', 'file-api'].includes(definition.attachmentTransport))
      const previous = state.connections[definition.id]
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      requireValue(
        ['openai-compatible', 'deepseek'].includes(definition.provider),
        'unsupported_provider'
      )
      const url = new URL(definition.baseUrl)
      // 禁止在 URL 中内嵌用户名或密码，防止凭据随 baseUrl 泄露到前端。
      requireValue(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
      requireValue(
        Array.isArray(definition.models) &&
          new Set(definition.models.map((model) => model.id)).size === definition.models.length
      )
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
    const connection = state.connections[selection.connectionId]
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
