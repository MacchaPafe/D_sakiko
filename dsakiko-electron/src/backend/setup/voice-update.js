import { fingerprint, requireValue } from '../core/support.js'
import {
  configuredVoice,
  emotionNames,
  missingVoiceMaterials,
  setReplyVoice
} from '../../shared/reply-voice.js'

function parts(voice) {
  return {
    gptWeights: voice?.gptWeights,
    sovitsWeights: voice?.sovitsWeights,
    defaultReference: voice?.defaultReference,
    ...voice?.emotions
  }
}

/** @param {object} voice 包内形态材料。 @returns {object} 比对基线，仅记录指纹，不持有旧版大文件。 */
export function voiceBaseline(voice) {
  return Object.fromEntries(
    Object.entries(parts(voice))
      .filter(([, v]) => v)
      .map(([k, v]) => [k, fingerprint(v)])
  )
}

const names = {
  gptWeights: 'GPT 模型',
  sovitsWeights: 'SoVITS 模型',
  defaultReference: '默认参考',
  ...emotionNames
}

/** @param {object} form 当前形态。 @param {object} incoming 新包材料。 @param {object} baseline 上次包内各项的指纹。 @returns {object[]} 有变化的更新项及默认选择。 */
export function voiceChanges(form, incoming, baseline, assetName = (asset) => asset.id) {
  function describe(material) {
    if (!material) return '未配置'
    if (material.id) return assetName(material)
    return (
      [material.audio && assetName(material.audio), material.text, material.language]
        .filter(Boolean)
        .join(' · ') || '未配置'
    )
  }
  const current = parts(configuredVoice(form))
  const next = parts(incoming)
  const changes = []
  for (const key of new Set([...Object.keys(next), ...Object.keys(baseline)])) {
    const before = fingerprint(current[key] ?? null)
    if (before === fingerprint(next[key] ?? null)) continue
    const modified = baseline[key] ? baseline[key] !== before : Boolean(current[key])
    changes.push({
      key,
      label: names[key] ?? key,
      before,
      modified,
      removed: !next[key],
      currentMaterial: describe(current[key]),
      newMaterial: describe(next[key])
    })
  }
  return changes
}

/** 用户确认后仍逐项检查当前值，下载期间编辑过的材料继续保留。 */
export function applyVoiceChanges(form, incoming, choices, packageId) {
  const materials = structuredClone(configuredVoice(form) ?? { emotions: {} })
  materials.emotions ??= {}
  const current = parts(materials)
  const next = parts(incoming)
  let changed = false
  for (const { key, before } of choices) {
    requireValue(Object.hasOwn(names, key), 'invalid_voice_selection')
    if (fingerprint(current[key] ?? null) !== before) continue
    const group = Object.hasOwn(emotionNames, key) ? materials.emotions : materials
    if (next[key]) group[key] = structuredClone(next[key])
    else delete group[key]
    changed = true
  }
  const enabled = form.replyVoiceEnabled ?? Boolean(form.voice || (!form.voiceExplicit && changed))
  setReplyVoice(form, materials, enabled && !missingVoiceMaterials(materials).length)
  form.voicePackageId = packageId
  form.voiceBaseline = voiceBaseline(incoming)
}
