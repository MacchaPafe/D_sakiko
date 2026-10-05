import { readdir, stat } from 'node:fs/promises'
import { join, relative, basename, dirname } from 'node:path'
import { createHash } from 'node:crypto'
import { JsonStore, readJson } from '../storage/files.js'
import { problemError } from '../../shared/contracts/desktop.js'
import { readModelCatalog } from '../../shared/contracts/model-catalog.js'

/**
 * @typedef {object} CharacterModelOption
 * @property {string} id 由角色和相对位置确定的身份，前端不提交文件路径。
 * @property {string} label 装扮名称。
 * @property {string} relativePath 相对角色目录的入口位置，仅用于辨识。
 * @property {'v2'|'v3'|null} version 模型运行时版本。
 * @property {string|null} problem 不可选原因。
 */

async function findModels(folder, depth = 0) {
  if (depth > 8) return []
  const entries = await readdir(folder, { withFileTypes: true })
  const result = []
  for (const entry of entries) {
    if (entry.name.startsWith('.')) continue
    const path = join(folder, entry.name)
    if (entry.isDirectory()) result.push(...(await findModels(path, depth + 1)))
    else if (entry.isFile() && /\.model3?\.json$/i.test(entry.name)) result.push(path)
  }
  return result.sort()
}

function modelLabel(path) {
  const parts = path.split('/')
  if (parts[0] === 'live2D_model') return '默认模型'
  if (parts[0] === 'live2D_model_costume') return '演出服（旧版）'
  if (parts[0] === 'extra_model' && parts.length > 2) return parts.slice(1, -1).join(' / ')
  return dirname(path) === '.' ? basename(path) : dirname(path)
}

async function readModel(path) {
  const json = await readJson(path)
  if (typeof json?.FileReferences?.Moc === 'string') return { json, version: 'v3' }
  if (typeof json?.model === 'string') return { json, version: 'v2' }
  throw problemError('invalid_model', '模型入口缺少有效的模型文件引用。')
}

/** 只读共享目录；装扮偏好与旧程序配置分离，私有路径不会进入桌面协议。 */
export class CharacterModels {
  entries = new Map()
  constructor(dataRoot, assets) {
    this.preferences = new JsonStore(join(dataRoot, 'character-models'))
    this.assets = assets
  }
  async discover(characterId, folder, legacyPath) {
    const paths = await findModels(folder)
    if (legacyPath && !paths.includes(legacyPath)) paths.push(legacyPath)
    const entries = new Map()
    for (const path of paths) {
      const relativePath = relative(folder, path).replaceAll('\\', '/')
      const id = createHash('sha256')
        .update(`${characterId}/${relativePath}`)
        .digest('hex')
        .slice(0, 24)
      const option = {
        id,
        label: modelLabel(relativePath),
        relativePath,
        version: null,
        problem: null
      }
      try {
        option.version = (await readModel(path)).version
      } catch {
        option.problem = '模型入口无法读取或格式不受支持。'
      }
      entries.set(id, {
        path,
        option,
        modified: (await stat(path).catch(() => null))?.mtimeMs || 0
      })
    }
    const saved = await this.preferences.load(characterId)
    const defaults = [...entries.values()].filter((entry) =>
      entry.option.relativePath.startsWith('live2D_model/')
    )
    defaults.sort((a, b) => b.modified - a.modified)
    const initial = [...entries.values()].find((entry) => entry.path === legacyPath) || defaults[0]
    const selectedModelId = saved?.modelId || initial?.option.id || null
    if (saved && !entries.has(saved.modelId)) {
      // 保留缺失的选择，避免资源盘暂时断开时悄悄改成另一套服装。
      entries.set(saved.modelId, {
        path: null,
        option: {
          id: saved.modelId,
          label: saved.label || '已保存的模型',
          relativePath: saved.relativePath || '',
          version: null,
          problem: '找不到已保存的模型，请重新选择装扮。'
        }
      })
    }
    this.entries.set(characterId, entries)
    return { models: [...entries.values()].map((entry) => entry.option), selectedModelId }
  }
  async resolve(characterId, modelId) {
    const entry = this.entries.get(characterId)?.get(modelId)
    if (!entry?.path || entry.option.problem)
      throw problemError(
        'model_unavailable',
        entry?.option.problem || '该模型不属于此角色，请重新选择。'
      )
    const { json } = await readModel(entry.path)
    const catalog = readModelCatalog(json)
    return {
      presentation: {
        model: await this.assets.register(entry.path, 'external', true),
        mapping: null
      },
      performances: {
        motions: catalog.motions.map(({ id, description }) => ({ id, description })),
        expressions: catalog.expressions.map(({ id, description }) => ({ id, description }))
      }
    }
  }
  /**
   * 校验目录中的候选项并原子保存选择，读取失败时保留原设置。
   * @param {string} characterId 角色身份。
   * @param {string} modelId 已发现的模型身份。
   * @returns {Promise<object>} 新模型的展示及动作、表情能力。
   */
  async select(characterId, modelId) {
    const capabilities = await this.resolve(characterId, modelId)
    const previous = await this.preferences.load(characterId)
    const option = this.entries.get(characterId).get(modelId).option
    await this.preferences.save({
      id: characterId,
      revision: previous?.revision || 0,
      modelId,
      label: option.label,
      relativePath: option.relativePath
    })
    return capabilities
  }
}
