import path from 'node:path'
import { inspectModelFolder } from '../live2d-import/interface.js'

/**
 * @param {import('../../shared/contracts/characters.js').CharacterForm} [form] 接收角色的现有形态。
 * @param {string} [id] 旧版本归一化得到的模型 ID。
 * @returns {string|undefined} 实际安装的模型 ID。
 */
export function importedOutfitId(form, id) {
  if (!id) return undefined
  return form?.models.find((model) => model.id === id || model.legacyIds?.includes(id))?.id
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
 * @param {import('../../shared/contracts/characters.js').CharacterDefinition} incoming 同一角色的旧版定义。
 * @param {import('../resources/interface.js').MediaAssets} assets 媒体库。
 * @param {AbortSignal} signal 导入取消信号。
 * @returns {Promise<boolean>} 是否增加了服装或旧版模型身份映射。
 */
export async function mergeLegacyOutfits(existing, incoming, assets, signal) {
  let changed = false
  const used = new Set(existing.forms.flatMap((form) => form.models.map(({ id }) => id)))
  for (const form of existing.forms) {
    const source = incoming.forms.find((item) => item.id === form.id)
    const missing =
      source?.models.filter((model) => !used.has(model.id) && !importedOutfitId(form, model.id)) ??
      []
    if (!missing.length) continue
    const signatures = new Map()
    for (const model of form.models) {
      const key = await digest(assets, model, signal)
      if (key && !signatures.has(key)) signatures.set(key, model)
    }
    for (const model of missing) {
      const key = await digest(assets, model, signal)
      const equivalent = signatures.get(key)
      if (equivalent) {
        equivalent.legacyIds = [...(equivalent.legacyIds ?? []), model.id]
      } else {
        form.models.push(model)
        if (key) signatures.set(key, model)
      }
      used.add(model.id)
      changed = true
    }
    form.defaultModelId ??= form.models[0]?.id ?? null
  }
  return changed
}
