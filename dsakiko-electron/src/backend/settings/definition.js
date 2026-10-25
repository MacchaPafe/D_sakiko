import { validSpeechResources } from '../../shared/contracts/speech-resources.js'
import { ConfigItem, config, isConfigObject, parseConfig } from '../core/config.js'

/** 模型生成参数的字段定义。连接 ID 与模型 ID 无固定默认值，需由用户显式选择。 */
export const generationDefinition = {
  connectionId: config.string({ optional: true, minLength: 1 }),
  modelId: config.string({ optional: true, minLength: 1 }),
  language: config.enum({ default: 'Japanese', values: ['Japanese', 'Chinese'] }),
  translationLanguage: config.enum({ default: 'Chinese', values: [null, 'Japanese', 'Chinese'] }),
  temperature: config.number({ default: 1, min: 0, max: 2 }),
  topP: config.number({ default: 1, exclusiveMin: 0, max: 1 }),
  timeoutMs: config.number({ default: 60000, integer: true, min: 1, max: 3600000 }),
  maxOutputTokens: config.number({ default: 4096, integer: true, min: 1, max: 3600000 }),
  reasoning: {
    enabled: config.boolean({ default: true }),
    effort: new ConfigItem({
      default: null,
      validate: (value) => value === null || typeof value === 'string',
      message: '必须为字符串或 null'
    })
  },
  compression: {
    enabled: config.boolean({ default: true }),
    thresholdTokens: config.number({ default: 258000, integer: true, min: 1 }),
    retainedTurns: config.number({ default: 2, integer: true, min: 1 })
  },
  contextTokenBudget: config.number({ default: 300000, integer: true, min: 1, max: 3600000 }),
  limits: {
    maxSteps: config.number({ default: 15, integer: true, min: 1, max: 100 }),
    maxRequestRetries: config.number({ default: 2, integer: true, min: 0, max: 100 }),
    maxFormatRepairs: config.number({ default: 2, integer: true, min: 0, max: 100 })
  }
}

const samplingDefinition = {
  topK: config.number({ integer: true, min: 1 }),
  topP: config.number({ exclusiveMin: 0, max: 1 }),
  temperature: config.number({ exclusiveMin: 0 }),
  seed: config.number({ integer: true }),
  sampleSteps: config.number({ integer: true, min: 1 })
}

/** 语音合成参数的字段定义。其中采样参数（sampling）作为单个可选配置项，提供时必须包含完整的 5 个字段。 */
export const speechDefinition = {
  enabled: config.boolean({ default: false }),
  speed: config.number({ default: 1, exclusiveMin: 0, max: 10 }),
  sentencePauseMs: config.number({ default: 300, integer: true, min: 0, max: 60000 }),
  sampling: new ConfigItem({
    optional: true,
    validate(value) {
      try {
        parseConfig(samplingDefinition, value)
        return true
      } catch {
        return false
      }
    },
    correct: (value) => parseConfig(samplingDefinition, value, { correct: true }),
    message: '必须提供完整采样参数：topK、topP、temperature、seed、sampleSteps，并满足各自数值范围'
  })
}

/** 全局设置项的字段定义（不包含版本号、连接列表与密钥）。 */
export const settingsDefinition = {
  generation: generationDefinition,
  speech: speechDefinition,
  speechStartup: {
    resources: new ConfigItem({
      default: { shared: {}, versions: {} },
      validate: validSpeechResources,
      correct: (value) => value,
      message: '必须提供由逐文件 AssetRef 组成的 shared 与 versions 清单'
    }),
    device: config.enum({ default: 'cpu', values: ['cpu', 'cuda', 'mps'] }),
    precision: config.enum({ default: 'float32', values: ['float32', 'float16'] }),
    architecture: config.string({ default: 'v2', minLength: 1 }),
    maxConcurrentInferences: config.number({ default: 1, integer: true, min: 1 }),
    maxResidentModels: config.number({ default: 1, integer: true, min: 1 })
  },
  bgm: {
    audio: new ConfigItem({
      default: null,
      validate: (value) =>
        value === null ||
        (isConfigObject(value) &&
          Object.keys(value).length === 1 &&
          typeof value.id === 'string' &&
          /^[\p{L}\p{N}_-]{1,100}$/u.test(value.id)),
      // 为什么不在自动修复时将非法资源引用重置为 null：
      // 避免因传参格式错误而静默清空用户已选中的背景音乐。
      correct: (value) => value,
      message: '必须为仅含合法 id 的资源引用或 null'
    }),
    enabled: config.boolean({ default: false }),
    loop: config.boolean({ default: true })
  },
  display: {
    frameRate: config.number({ default: 60, exclusiveMin: 0 }),
    theme: config.enum({ default: 'system', values: ['system', 'light', 'dark'] }),
    startInPetMode: config.boolean({ default: false })
  }
}

/** 递归冻结整个配置定义树，防止外部意外修改公共配置规则。 */
function freezeDefinition(definition) {
  for (const item of Object.values(definition)) {
    if (!(item instanceof ConfigItem)) freezeDefinition(item)
  }
  return Object.freeze(definition)
}
freezeDefinition(settingsDefinition)

/**
 * 创建一个填充了所有默认值的初始全局设置对象。
 * @returns {import('../../shared/contracts/settings.js').GlobalSettings} 初始全局设置对象。
 */
export function createDefaultSettings() {
  return { revision: 0, ...parseConfig(settingsDefinition, {}, { defaults: true }) }
}
