import path from 'node:path'
import { inspectModelFolder } from '../live2d-import/interface.js'
import { requireValue } from '../core/support.js'

function installedOutfit(form, id) {
  return form?.models.find((model) => model.id === id || model.legacyIds?.includes(id))
}

/**
 * @param {import('../../shared/contracts/characters.js').CharacterForm} [form] 接收角色的现有形态。
 * @param {string} [id] 旧版本归一化得到的模型 ID。
 * @returns {string|undefined} 实际安装的模型 ID。
 */
export function importedOutfitId(form, id) {
  if (!id) return undefined
  return installedOutfit(form, id)?.id
}

/** @param {object} existing 已有角色。 @param {object} incoming 扫描所得角色。 @returns {Map<object, object[]>} 各形态需要追加或补回资源引用的旧服装，供计划和提交共同使用。 */
export function legacyOutfitsToMerge(existing, incoming) {
  const result = new Map()
  const used = new Set(existing.forms.flatMap((form) => form.models.map(({ id }) => id)))
  for (const form of existing.forms) {
    const source = incoming.forms.find((item) => item.id === form.id)
    const models = (source?.models ?? []).filter((model) => {
      const installed = installedOutfit(form, model.id)
      if (installed) return !installed.presentation?.model
      return !used.has(model.id)
    })
    if (models.length) result.set(form, models)
  }
  return result
}

/** @param {object} model 扫描到的旧模型。 @param {object} refs 已准备资源。 @returns {object} 具有有效资源引用的服装配置。 */
export function legacyOutfitDefinition(model, refs) {
  const asset = refs[model.key]
  requireValue(asset?.id, 'legacy_plan_stale', '导入计划缺少模型资源，请重新扫描并创建计划。')
  return { id: model.id, name: model.name, presentation: { model: asset } }
}

async function digest(assets, model, signal) {
  try {
    const file = await assets.localPath(model.presentation.model)
    return (await inspectModelFolder(path.dirname(file), signal, path.basename(file))).digest
  } catch {
    signal?.throwIfAborted()
    // 旧包可能不符合现在的本地导入要求；仍保留该服装和原有身份。
    return null
  }
}

/**
 * @param {import('../../shared/contracts/characters.js').CharacterDefinition} existing 已有角色。
 * @param {object} incoming 同一角色的扫描结果。
 * @param {object} refs 已准备的资源引用。
 * @param {import('../resources/interface.js').MediaAssets} assets 媒体库。
 * @param {AbortSignal} signal 导入取消信号。
 * @returns {Promise<boolean>} 是否增加了服装或旧版模型身份映射。
 */
export async function mergeLegacyOutfits(existing, incoming, refs, assets, signal) {
  let changed = false
  for (const [form, missing] of legacyOutfitsToMerge(existing, incoming)) {
    const signatures = new Map()
    for (const model of form.models) {
      const key = await digest(assets, model, signal)
      if (key && !signatures.has(key)) signatures.set(key, model)
    }
    for (const scanned of missing) {
      const model = legacyOutfitDefinition(scanned, refs)
      const installed = installedOutfit(form, model.id)
      if (installed) {
        // 修复旧导入器留下的空引用，保留服装 ID、用户命名及对话中已固定的选择。
        installed.presentation = { ...installed.presentation, model: model.presentation.model }
        const key = await digest(assets, installed, signal)
        if (key) signatures.set(key, installed)
        changed = true
        continue
      }
      const key = await digest(assets, model, signal)
      const equivalent = signatures.get(key)
      if (equivalent) {
        equivalent.legacyIds = [...(equivalent.legacyIds ?? []), model.id]
      } else {
        form.models.push(model)
        if (key) signatures.set(key, model)
      }
      changed = true
    }
    form.defaultModelId ??= form.models[0]?.id ?? null
  }
  return changed
}
