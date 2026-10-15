import { fields, requireValue } from './support.js'

/**
 * 校验模型生成配置项的字段名与取值范围。
 * @param {import('../../shared/contracts/settings.js').GenerationOptions} value 待校验的生成配置。
 * @returns {void}
 */
export function generationOptions(value) {
  fields(value, [
    'connectionId',
    'modelId',
    'language',
    'translationLanguage',
    'temperature',
    'topP',
    'timeoutMs',
    'maxOutputTokens',
    'reasoning',
    'compression',
    'contextTokenBudget',
    'limits'
  ])
  for (const key of ['connectionId', 'modelId'])
    if (key in value) requireValue(typeof value[key] === 'string' && value[key].length > 0)
  if ('language' in value) requireValue(['Japanese', 'Chinese'].includes(value.language))
  if ('translationLanguage' in value)
    requireValue([null, 'Japanese', 'Chinese'].includes(value.translationLanguage))
  if ('temperature' in value)
    requireValue(
      Number.isFinite(value.temperature) && value.temperature >= 0 && value.temperature <= 2
    )
  if ('topP' in value)
    requireValue(Number.isFinite(value.topP) && value.topP > 0 && value.topP <= 1)
  for (const key of ['timeoutMs', 'maxOutputTokens', 'contextTokenBudget'])
    if (key in value)
      requireValue(Number.isInteger(value[key]) && value[key] > 0 && value[key] <= 3600000)
  if (value.reasoning) {
    fields(value.reasoning, ['enabled', 'effort'])
    requireValue(
      typeof value.reasoning.enabled === 'boolean' &&
        (value.reasoning.effort === null || typeof value.reasoning.effort === 'string')
    )
  }
  if (value.compression) {
    fields(value.compression, ['enabled', 'thresholdTokens', 'retainedTurns'])
    requireValue(
      typeof value.compression.enabled === 'boolean' &&
        Number.isInteger(value.compression.thresholdTokens) &&
        value.compression.thresholdTokens > 0 &&
        Number.isInteger(value.compression.retainedTurns) &&
        value.compression.retainedTurns >= 1
    )
  }
  if (value.limits) {
    fields(value.limits, ['maxSteps', 'maxRequestRetries', 'maxFormatRepairs'])
    for (const number of Object.values(value.limits))
      requireValue(Number.isInteger(number) && number >= 0 && number <= 100)
    requireValue(value.limits.maxSteps > 0)
  }
}

/**
 * 校验语音合成配置项的字段名与取值范围。
 * @param {import('../../shared/contracts/settings.js').SpeechOptions} value 待校验的语音配置。
 * @returns {void}
 */
export function speechOptions(value) {
  fields(value, ['enabled', 'speed', 'sentencePauseMs', 'sampling'])
  if ('enabled' in value) requireValue(typeof value.enabled === 'boolean')
  if ('speed' in value)
    requireValue(Number.isFinite(value.speed) && value.speed > 0 && value.speed <= 10)
  if ('sentencePauseMs' in value)
    requireValue(
      Number.isInteger(value.sentencePauseMs) &&
        value.sentencePauseMs >= 0 &&
        value.sentencePauseMs <= 60000
    )
  if (value.sampling) {
    fields(value.sampling, ['topK', 'topP', 'temperature', 'seed', 'sampleSteps'])
    requireValue(
      Number.isInteger(value.sampling.topK) &&
        value.sampling.topK > 0 &&
        Number.isInteger(value.sampling.sampleSteps) &&
        value.sampling.sampleSteps > 0 &&
        Number.isInteger(value.sampling.seed)
    )
    requireValue(
      Number.isFinite(value.sampling.topP) &&
        value.sampling.topP > 0 &&
        value.sampling.topP <= 1 &&
        Number.isFinite(value.sampling.temperature) &&
        value.sampling.temperature > 0
    )
  }
}

/**
 * 校验对话的 Live2D 场景与角色布局配置。
 * @param {import('../../shared/contracts/settings.js').ChatPresentationSettings} value 待校验的场景展示配置。
 * @param {string[]} characterIds 当前对话中包含的角色 ID 列表。
 * @returns {void}
 */
export function presentationOptions(value, characterIds) {
  fields(value, ['background', 'characters'])
  // 限制只能为当前对话已有的角色配置布局，防止通过设置接口越界塞入不属于该对话的角色。
  fields(value.characters, characterIds)
  for (const layout of Object.values(value.characters)) {
    fields(layout, ['x', 'y', 'scale', 'facing'])
    requireValue(
      Number.isFinite(layout.x) &&
        Number.isFinite(layout.y) &&
        Number.isFinite(layout.scale) &&
        layout.scale > 0 &&
        ['left', 'right'].includes(layout.facing)
    )
  }
}
