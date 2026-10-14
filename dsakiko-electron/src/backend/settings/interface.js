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
 * @property {import('../../shared/contracts/settings.js').GlobalSettings} settings 可公开的共享设置。
 * @property {Record<string, import('../../shared/contracts/models.js').ConnectionDefinition>} connections 连接目录。
 * @property {Record<string, string>} secrets 私有凭据；只在后端文件与内存中存在，不随 settings 返回。
 */

/**
 * 全局设置与连接目录负责校验、默认值及格式迁移；不拥有 Chat，也不改变当前执行材料。
 * 全局/Chat/本次同名字段只在 Conversation 开始时解析一次。必需连接和资源缺失明确拒绝。
 * 语音设备/精度/结构/预算下次启动生效；生成默认值下轮生效；BGM 曲目/开关及展示偏好由装配协调。
 * 播放音量由各客户端在前端持久化，本模块不保存、不分发，也不在新客户端连接时提供音量默认值。
 * 旧设置中的 playback 和 bgm.volume 在迁移时丢弃，不导入客户端；客户端无本地值时使用 100%。
 */
export class Settings {
  /**
   * 绑定设置存储和显式连接探测能力。
   * @param {object} options 宿主依赖。
   * @param {string} options.directory 私有数据目录。
   * @param {((connection: import('../../shared/contracts/models.js').ResolvedModelConnection, signal?: AbortSignal) => Promise<unknown>) | null} [options.probe] 主动探测；普通读取不会调用模型。
   */
  constructor({ directory, probe = null }) {
    this.file = path.join(directory, 'settings.json')
    this.queue = new SerialQueue()
    this.scopes = new Map()
    this.bus = new SnapshotBus()
    this.probe = probe
  }
  /** 读取私有存储封套，移除旧共享音量；不能把此结果直接交给客户端。
   * @returns {Promise<SettingsState>} 本次读改写使用的独立材料。
   */
  async state() {
    const state = await readJson(this.file, {
      settings: copy(defaultSettings),
      connections: {},
      secrets: {}
    })
    delete state.settings.playback
    if (state.settings.bgm) delete state.settings.bgm.volume
    return state
  }
  /**
   * 观察共享设置，首次订阅即发送当前值。
   * @param {(settings: import('../../shared/contracts/settings.js').GlobalSettings) => void} listener 完整快照接收者。
   * @returns {Promise<() => void>} 取消观察，不修改设置。
   */
  async observe(listener) {
    return this.bus.subscribe('settings', listener, await this.get())
  }
  /**
   * 取出本次解析固定的凭据；修改连接不改变已经运行的请求。
   * @param {string} scope resolveConnection 分配的临时作用域。
   * @returns {string} 仅供供应商适配器使用的凭据，未配置时为空。
   */
  credential(scope) {
    requireValue(this.scopes.has(scope), 'credential_expired')
    return this.scopes.get(scope)
  }

  /**
   * 读取当前全局设置，供设置页面展示及后续执行解析默认值。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 独立只读配置，不含凭据。
   */
  async get() {
    return copy((await this.state()).settings)
  }

  /**
   * 校验并保存完整的全局设置。
   * @param {import('../../shared/contracts/settings.js').GlobalSettings} settings 完整配置及原 revision；未知业务字段拒绝。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 原子比较递增后的新版本，不修改输入。
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
   * 列出已配置的模型连接及其能力信息，供选择和管理连接。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition[]>} 连接/能力/凭据存在标记，不返回密钥。
   */
  async listConnections() {
    return Object.values((await this.state()).connections).map(copy)
  }

  /**
   * 创建或更新一个模型连接，并按明确指令保留、替换或移除其凭据。
   * @param {import('../../shared/contracts/models.js').ConnectionDefinition} definition 原版本，0 只用于新建；credentialConfigured 为只读投影。
   * @param {{ kind: 'keep' } | { kind: 'replace', secret: string } | { kind: 'remove' }} credential 仅受控设置入口可写，不回显、记录或归档 secret。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition>} 已保存连接及递增版本。
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
   * 删除指定的模型连接配置。
   * @param {string} connectionId 目标。
   * @param {number} expectedRevision 防止删除较新配置。
   * @returns {Promise<void>} 已删除；已有请求保留固定材料，后续解析缺失引用明确失败。
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
   * 将连接和模型选择解析为一次后端请求可使用的完整连接及能力快照。
   * 后端专用解析，不向 renderer/归档发送凭据作用域。RemoteFiles 与请求使用同一快照。
   * @param {{ connectionId: string, modelId: string }} selection 模型选择。
   * @returns {Promise<import('../../shared/contracts/models.js').ResolvedModelConnection>} 完整临时连接及合并能力来源。
   */
  async resolveConnection(selection) {
    const state = await this.state()
    const connection = state.connections[selection.connectionId]
    const model = connection?.models.find((item) => item.id === selection.modelId)
    requireValue(model, 'model_unavailable', '模型连接或模型不存在。')
    // 作用域保存解析时的 secret，避免进行中的请求因用户换密钥而改变身份。
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
   * 显式连接检查，允许最小真实请求；不执行工具、不写聊天，也不把成功文本请求当作视觉能力证明。
   * @param {{ connectionId: string, modelId: string }} selection 要检查的连接及模型。
   * @param {AbortSignal} signal 取消。
   * @returns {Promise<{ reachable: boolean, problems: import('../../shared/contracts/common.js').Problem[] }>} 脱敏检查结果。
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
