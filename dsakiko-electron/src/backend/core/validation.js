import { fields, requireValue, safeId } from './support.js'
import { parseConfig } from './config.js'
import { generationDefinition, speechDefinition } from '../settings/definition.js'

/**
 * 校验对话级的模型生成覆盖配置。
 * @param {import('../../shared/contracts/settings.js').GenerationOptions} value 待校验的生成配置。
 * @returns {void}
 */
export function generationOptions(value) {
  parseConfig(generationDefinition, value, { partial: true, path: 'generation' })
}

/**
 * 校验对话级的语音合成覆盖配置。
 * @param {import('../../shared/contracts/settings.js').SpeechOptions} value 待校验的语音配置。
 * @returns {void}
 */
export function speechOptions(value) {
  parseConfig(speechDefinition, value, { partial: true, path: 'speech' })
}

/**
 * 校验对话的 Live2D 场景与角色布局配置。
 * @param {import('../../shared/contracts/settings.js').ChatPresentationSettings} value 待校验的场景展示配置。
 * @param {string[]} characterIds 当前对话中包含的角色 ID 列表。
 * @returns {void}
 */
export function presentationOptions(value, characterIds) {
  fields(value, ['background', 'characters', 'orientation', 'frames'])
  if (value.background != null) {
    fields(value.background, ['id'])
    safeId(value.background.id)
  }
  if (value.orientation !== undefined)
    requireValue(['screen', 'face-to-face'].includes(value.orientation))
  if (value.frames) {
    fields(value.frames, characterIds)
    for (const outfits of Object.values(value.frames)) {
      requireValue(outfits && typeof outfits === 'object' && !Array.isArray(outfits))
      for (const frame of Object.values(outfits)) {
        fields(frame, ['scale', 'offsetX', 'offsetY'])
        requireValue(
          Number.isFinite(frame.scale) &&
            frame.scale >= 0.2 &&
            frame.scale <= 5 &&
            Number.isFinite(frame.offsetX) &&
            Number.isFinite(frame.offsetY)
        )
      }
    }
  }
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
