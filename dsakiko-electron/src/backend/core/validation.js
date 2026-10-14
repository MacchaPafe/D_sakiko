import { fields, requireValue } from './support.js'

/**
 * 校验生成覆盖值；省略仍表示继承，不会在校验时写入默认值。
 * @param {import('../../shared/contracts/settings.js').GenerationOptions} value 待保存或待执行的覆盖值。
 * @returns {void} 非法字段或范围抛出业务错误，不修改输入。
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
 * 校验合成选项，避免把未知设置当作已经应用。
 * @param {import('../../shared/contracts/settings.js').SpeechOptions} value 合成覆盖值。
 * @returns {void} 非法字段或范围抛出业务错误，不修改输入。
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
 * 校验场景布局；页面不能经设置偷偷增加角色或序列。
 * @param {import('../../shared/contracts/settings.js').ChatPresentationSettings} value 对话展示设置。
 * @param {string[]} characterIds 对话已经固定的角色身份。
 * @returns {void} 越界角色或非法布局抛出业务错误。
 */
export function presentationOptions(value, characterIds) {
  fields(value, ['background', 'characters'])
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
