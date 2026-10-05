import { join, isAbsolute } from 'node:path'
import { readJson, writeJson, SerialQueue } from '../storage/files.js'
import { problemError } from '../../shared/contracts/desktop.js'

/**
 * @typedef {object} PrototypeSettings
 * @property {string} resourceRoot 旧资源根目录，只读。
 * @property {string} pythonExecutable Python 解释器。
 * @property {string} baseUrl 模型连接地址。
 * @property {string} apiKey 仅在后端存放的凭据。
 * @property {string} model 模型名称。
 * @property {'ja'|'zh'} language 台词与合成语言。
 * @property {boolean} voiceEnabled 是否合成语音。
 * @property {number} maxConcurrent 同时推理上限。
 * @property {number} maxResident 常驻实例上限。
 * @property {'cpu'|'mps'|'cuda'} device 推理设备。
 */

/** 只负责设置；各模块获得自身所需的配置投影。 */
export class Settings {
  queue = new SerialQueue()
  constructor(directory, defaults = {}) {
    this.path = join(directory, 'settings.json')
    this.defaults = {
      resourceRoot: '',
      pythonExecutable: 'python3',
      baseUrl: '',
      apiKey: '',
      model: '',
      language: 'ja',
      voiceEnabled: true,
      maxConcurrent: 1,
      maxResident: 2,
      device: 'cpu',
      temperature: 0.8,
      requestTimeoutMs: 120000,
      contextTokenBudget: 12000,
      ...defaults
    }
  }
  async initialize() {
    this.value = { ...this.defaults, ...(await readJson(this.path, {})) }
    return this
  }
  get() {
    return structuredClone(this.value)
  }
  publicValue() {
    const value = this.get()
    value.hasApiKey = Boolean(value.apiKey)
    value.apiKey = ''
    return value
  }
  save(changes) {
    return this.queue.run('settings', async () => {
      const value = { ...this.value }
      for (const key of Object.keys(this.defaults)) {
        if (Object.hasOwn(changes, key) && key !== 'apiKey') value[key] = changes[key]
      }
      if (changes.clearApiKey === true) value.apiKey = ''
      if (typeof changes.apiKey === 'string' && changes.apiKey.trim())
        value.apiKey = changes.apiKey.trim()
      for (const key of ['resourceRoot', 'pythonExecutable', 'baseUrl', 'model']) {
        if (typeof value[key] !== 'string') throw problemError('invalid_settings', '设置文本无效。')
        value[key] = value[key].trim()
      }
      if (value.resourceRoot && !isAbsolute(value.resourceRoot))
        throw problemError('invalid_settings', '资源目录必须是绝对路径。')
      if (value.baseUrl) {
        let url
        try {
          url = new URL(value.baseUrl)
        } catch {
          throw problemError('invalid_settings', '模型地址无效。')
        }
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
          throw problemError('invalid_settings', '请使用不含凭据的 HTTP(S) 模型地址。')
      }
      if (
        !['ja', 'zh'].includes(value.language) ||
        !['cpu', 'mps', 'cuda'].includes(value.device) ||
        typeof value.voiceEnabled !== 'boolean'
      )
        throw problemError('invalid_settings', '语言、设备或语音开关无效。')
      for (const key of ['maxConcurrent', 'maxResident'])
        if (!Number.isInteger(value[key]) || value[key] < 1 || value[key] > 4)
          throw problemError('invalid_settings', '执行槽与常驻上限必须为 1–4。')
      if (!Number.isFinite(value.temperature) || value.temperature < 0 || value.temperature > 2)
        throw problemError('invalid_settings', '温度必须在 0–2 之间。')
      if (
        !Number.isInteger(value.requestTimeoutMs) ||
        value.requestTimeoutMs < 1000 ||
        value.requestTimeoutMs > 600000
      )
        throw problemError('invalid_settings', '请求超时无效。')
      if (
        !Number.isInteger(value.contextTokenBudget) ||
        value.contextTokenBudget < 1000 ||
        value.contextTokenBudget > 100000
      )
        throw problemError('invalid_settings', '上下文预算无效。')
      await writeJson(this.path, value)
      this.value = value
      return this.publicValue()
    })
  }
  async importLegacy() {
    const root = this.value.resourceRoot
    if (!root) throw problemError('missing_resources', '请先选择旧程序的资源根目录。')
    const legacy = await readJson(join(root, 'd_sakiko_config.json'))
    if (!legacy) throw problemError('missing_settings', '目录中没有 d_sakiko_config.json。')
    const llm = legacy.llm_setting || {}
    const custom = llm.enable_custom_llm_api_provider === true
    const provider = llm.llm_api_provider || 'deepseek'
    function providerValue(value) {
      if (typeof value === 'string') return value
      return value?.[provider] || ''
    }
    const defaults = { deepseek: 'https://api.deepseek.com', openai: 'https://api.openai.com/v1' }
    const baseUrl = custom
      ? llm.custom_llm_api_url
      : providerValue(llm.llm_api_base_url) || defaults[provider]
    if (!baseUrl)
      throw problemError(
        'missing_connection',
        '旧供应商没有兼容接口地址，请手动填写服务地址、模型和密钥。'
      )
    const storedModel = custom ? llm.custom_llm_api_model : providerValue(llm.llm_api_model)
    let model = storedModel || ''
    for (const prefix of ['openai/', `${provider}/`])
      if (model.startsWith(prefix)) model = model.slice(prefix.length)
    const audio = legacy.audio_setting || {}
    let device = 'cpu'
    if (audio.mps_enabled) device = 'mps'
    if (audio.cuda_enabled) device = 'cuda'
    return this.save({
      baseUrl,
      apiKey: custom ? llm.custom_llm_api_key : providerValue(llm.llm_api_key),
      clearApiKey: true,
      model,
      temperature: llm.llm_temperature ?? 0.8,
      device
    })
  }
}
