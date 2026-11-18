import path from 'node:path'
import { createHash } from 'node:crypto'
import { readFile, stat } from 'node:fs/promises'
import { copy, problem, requireValue } from '../core/support.js'
import { validModelFilePath } from '../resources/files.js'

const motionLabels = {
  nod: '点头',
  denial: '摇头或否定手势',
  bye: '告别手势',
  thinking: '思考动作',
  look: '转动视线或身体',
  idle: '待机动作'
}
const expressionLabels = {
  smile: '微笑',
  bsmile: '笑容',
  sad: '悲伤',
  cry: '哭泣表情',
  angry: '生气',
  serious: '认真',
  surprised: '惊讶',
  shy: '害羞',
  thinking: '思考',
  pale: '不安',
  idle: '平静',
  upset: '不悦',
  sneer: '冷笑',
  smirk: '带笑的表情',
  amazed: '惊讶',
  hatred: '愤恨'
}

/**
 * 模型目录同时保留完整资源与提示词描述；无描述的资源仍能用于动作组回退。
 * @typedef {object} ModelCatalog
 * @property {'v2' | 'v3'} version 模型格式。
 * @property {Object<string, {description: string, variants: Object<string, {member: string, entry: object, durationMs?: number}>}>} motions 逻辑动作及方向变体。
 * @property {Object<string, {description: string, member: string}>} expressions 表情资源。
 * @property {Object<string, string[]>} groups 模型原有动作组。
 * @property {object[]} presets 有效的动作和表情组合。
 * @property {object} states 状态声明。
 * @property {object} stateEffects 动作与表情的状态影响。
 * @property {import('../../shared/contracts/common.js').Problem[]} problems 标注或资源问题。
 */

function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

/** @param {string} source 动作文件文本。 @param {boolean} v3 是否使用新版动作格式。 @returns {number|null} 单次动作时长，无法解析时为空。 */
export function readMotionDurationMs(source, v3) {
  const text = source.replace(/^\uFEFF/u, '')
  let milliseconds
  if (v3) {
    try {
      const seconds = JSON.parse(text)?.Meta?.Duration
      if (typeof seconds !== 'number') return null
      milliseconds = Math.round(seconds * 1000)
    } catch {
      return null
    }
  } else {
    const declared = Number(text.match(/^\$fps\s*=\s*([^\s,]+)/mu)?.[1])
    const fps = declared > 5 && declared < 121 ? declared : 30
    let frames = 0
    // 与 Cubism 2 Web 一致，取所有曲线（含可见性和布局）的最长采样数。
    for (const track of text.matchAll(/^[A-Za-z_][^=\r\n]*=([^\r\n]*)/gmu)) {
      const samples = track[1]
        .split(/[\t ,]+/u)
        .filter((value) => value && Number.isFinite(Number(value)))
      frames = Math.max(frames, samples.length)
    }
    milliseconds = Math.trunc((frames * 1000) / fps)
  }
  return Number.isFinite(milliseconds) && milliseconds > 0 && milliseconds <= 86400000
    ? milliseconds
    : null
}

function compareFiles([left], [right]) {
  if (left < right) return -1
  if (left > right) return 1
  return 0
}

function identity(file, version) {
  const stem = file.replace(version === 'v3' ? /\.motion3\.json$/u : /\.mtn$/u, '')
  const match = version === 'v3' ? stem.match(/_([CLR])$/iu) : null
  return {
    family: match ? stem.slice(0, -2) : stem,
    direction: match ? match[1].toUpperCase() : ''
  }
}

function description(id, local, shared, labels) {
  const value = local[id] ?? shared[id]
  const text = typeof value === 'string' ? value : value?.description
  if (typeof text === 'string' && text.trim()) return text
  const tokens = id
    .toLowerCase()
    .replace(/[0-9]+/gu, '')
    .split(/[^a-z]+/u)
  return tokens.map((token) => labels[token]).find(Boolean) ?? ''
}

/**
 * 按 Python 的逻辑 ID、方向合并与描述继承规则建立目录，不改写原始模型文件。
 * @param {object} data 模型入口内容。
 * @param {object} config 模型专属 performance 标注。
 * @param {object} shared 角色共享标注。
 * @param {(file: string) => Promise<string | null>} locate 校验资源存在并返回包内路径。
 * @returns {Promise<ModelCatalog>} 可用于提示词和演出的同源目录。
 */
export async function buildModelCatalog(data, config, shared, locate) {
  const version = data.FileReferences ? 'v3' : 'v2'
  const refs = version === 'v3' ? object(data.FileReferences) : data
  const rawGroups = object(version === 'v3' ? refs.Motions : refs.motions)
  const catalog = {
    version,
    motions: Object.create(null),
    expressions: Object.create(null),
    groups: Object.create(null),
    presets: [],
    states: object(config.states),
    stateEffects: object(config.stateEffects),
    problems: []
  }
  const entries = new Map()
  const indexed = data.DSakiko?.MotionAssets
  const source = Array.isArray(indexed) ? [...indexed] : []
  for (const [group, values] of Object.entries(rawGroups))
    if (!group.startsWith('__') && Array.isArray(values)) source.push(...values)
  const fileOf = (entry) => (version === 'v3' ? entry?.File : entry?.file)
  for (const entry of source) {
    const file = fileOf(entry)
    if (typeof file === 'string' && !entries.has(file)) entries.set(file, entry)
  }
  const families = new Map()
  for (const file of entries.keys()) {
    const { family } = identity(file, version)
    const base = path.posix.basename(family)
    if (!families.has(base)) families.set(base, new Set())
    families.get(base).add(family)
  }
  const byFile = new Map()
  for (const [file, entry] of [...entries].sort(compareFiles)) {
    const member = await locate(file)
    if (!member) continue
    const { family, direction } = identity(file, version)
    const base = path.posix.basename(family)
    let id = base
    if (families.get(base).size > 1)
      id =
        createHash('sha256').update(path.posix.dirname(family)).digest('hex').slice(0, 8) +
        '_' +
        base
    if (typeof entry.LogicalId === 'string') id = entry.LogicalId
    if (typeof config.bindings?.[file] === 'string' && config.bindings[file].trim())
      id = config.bindings[file].trim()
    catalog.motions[id] ??= { description: '', variants: Object.create(null) }
    catalog.motions[id].variants[direction] = { member, entry: copy(entry) }
    byFile.set(file, id)
  }
  const expressions = version === 'v3' ? refs.Expressions : refs.expressions
  for (const entry of Array.isArray(expressions) ? expressions : []) {
    const id = version === 'v3' ? entry?.Name : entry?.name
    const member = await locate(fileOf(entry))
    if (typeof id === 'string' && member) catalog.expressions[id] = { description: '', member }
  }
  for (const [group, values] of Object.entries(rawGroups)) {
    if (group.startsWith('__') || !Array.isArray(values)) continue
    catalog.groups[group] = [
      ...new Set(values.map((entry) => byFile.get(fileOf(entry))).filter(Boolean))
    ]
  }
  const common = object(shared.series?.[config.series || 'default'])
  for (const [kind, labels] of [
    ['motions', motionLabels],
    ['expressions', expressionLabels]
  ])
    for (const [id, resource] of Object.entries(catalog[kind]))
      resource.description = description(id, object(config[kind]), object(common[kind]), labels)
  catalog.presets = (Array.isArray(config.presets) ? config.presets : [])
    .filter((item) => catalog.motions[item?.motion] && catalog.expressions[item?.expression])
    .map(copy)
  return catalog
}

/**
 * 从受管理资源包读取模型、同名标注与共享标注；显式 mapping 可使用原有 performance.json。
 * @param {import('../resources/interface.js').MediaAssets} assets 资源管理器。
 * @param {import('../../shared/contracts/presentation.js').ModelPresentation} presentation 模型材料。
 * @param {Map<string, {stamp: string, durationMs: number|null}> | null} [motionDurations] 单人演出使用的动作时长缓存。为空时只解析资源索引。
 * @returns {Promise<ModelCatalog>} 解析后的模型目录。
 */
export async function resolveModelCatalog(assets, presentation, motionDurations = null) {
  const entry = await assets.packageEntry(presentation.model)
  const data = JSON.parse(
    Buffer.from(await assets.read(presentation.model))
      .toString('utf8')
      .replace(/^\uFEFF/u, '')
  )
  requireValue(data && typeof data === 'object', 'invalid_model')
  const problems = []
  async function optional(member) {
    if (!member || !validModelFilePath(member)) return {}
    try {
      return JSON.parse(Buffer.from(await assets.read(presentation.model, member)).toString('utf8'))
    } catch (error) {
      if (error.problem?.code !== 'asset_unavailable') problems.push(problem(error))
      return {}
    }
  }
  const directory = entry ? path.posix.dirname(entry) : '.'
  let sharedDirectory = directory
  for (let parent = directory; parent !== '.'; parent = path.posix.dirname(parent)) {
    if (
      ['live2D_model', 'live2D_model_costume', 'extra_model'].includes(path.posix.basename(parent))
    ) {
      sharedDirectory = path.posix.dirname(parent)
      break
    }
  }
  const config = presentation.mapping
    ? JSON.parse(Buffer.from(await assets.read(presentation.mapping)).toString('utf8'))
    : await optional(entry?.replace(/\.json$/u, '.performance.json'))
  const shared = entry
    ? await optional(path.posix.join(sharedDirectory, 'performance.shared.json'))
    : {}
  const durationsByMember = new Map()
  async function locate(file) {
    // 包外路径不能绕过资源管理；仅导入入口单文件时不猜测磁盘旁的资源。
    if (
      !entry ||
      typeof file !== 'string' ||
      path.posix.isAbsolute(file) ||
      /[\\:#?%\0]/u.test(file)
    )
      return null
    const member = path.posix.join(directory, file)
    if (!validModelFilePath(member)) return null
    try {
      const target = await assets.localPath(presentation.model, member)
      const info = await stat(target)
      if (!info.isFile()) return null
      if (motionDurations && /(?:\.motion3\.json|\.mtn)$/iu.test(member)) {
        const stamp = [info.ino, info.size, info.mtimeMs, info.ctimeMs].join('/')
        let cached = motionDurations.get(target)
        if (cached?.stamp !== stamp) {
          const source = await readFile(target, 'utf8').catch(() => null)
          if (source !== null) {
            cached = {
              stamp,
              durationMs: readMotionDurationMs(source, /\.motion3\.json$/iu.test(member))
            }
            motionDurations.set(target, cached)
          }
        }
        if (cached?.stamp === stamp && cached.durationMs !== null)
          durationsByMember.set(member, cached.durationMs)
      }
      return member
    } catch (error) {
      problems.push({ ...problem(error), message: '模型成员不可用：' + member })
      return null
    }
  }
  const catalog = await buildModelCatalog(data, object(config), object(shared), locate)
  for (const { variants } of Object.values(catalog.motions))
    for (const variant of Object.values(variants)) {
      const durationMs = durationsByMember.get(variant.member)
      if (durationMs !== undefined) variant.durationMs = durationMs
    }
  catalog.problems.push(...problems)
  return catalog
}
