import path from 'node:path'
import { createHash } from 'node:crypto'
import { readFile, stat } from 'node:fs/promises'
import { writeJson } from '../core/files.js'

// 每一层是同等优先的文件名关键词。只有当前朝向没有可用成员时才取下一层。
const rules = {
  happiness: [
    ['smile', 'kime', 'wink'],
    ['play', 'action']
  ],
  sadness: [
    ['sad', 'cry'],
    ['serious', 'denial']
  ],
  anger: [['angry'], ['serious', 'denial', 'confrict']],
  disgust: [
    ['denial', 'confrict'],
    ['serious', 'angry']
  ],
  like: [['smile', 'kime', 'wink'], ['play']],
  surprise: [
    ['surprised', 'question'],
    ['action', 'play02']
  ],
  fear: [['nervous'], ['cry', 'serious', 'surprised', 'confrict']],
  IDLE: [['kime', 'nnf', 'smile01', 'wink', 'sleep', 'niyaniya', 'nf_left', 'nf_right']],
  text_generating: [['thinking'], ['check'], ['nod']],
  bye: [['bye', 'finish']],
  change_character: [
    ['bye', 'join', 'kime', 'smile', 'action', 'spin', 'maskoff'],
    ['play01', 'play02']
  ],
  idle_motion: [['idle']],
  talking_motion: [
    ['nod', 'sing'],
    ['play01', 'play02']
  ]
}

function classify(files) {
  const candidates = files.map((file) => {
    const stem = path.posix
      .basename(file)
      .toLowerCase()
      .replace(/\.motion3\.json$/u, '')
    const direction = stem.match(/_([clr])$/u)?.[1].toUpperCase()
    const name = stem.replace(/_([clr])$/u, '').replace(/^(mtn|motion)_/u, '')
    const tokens = name.split(/[^a-z0-9]+/u)
    const words = new Set([
      name,
      name.replace(/\d+$/u, ''),
      ...tokens,
      ...tokens.map((token) => token.replace(/\d+$/u, ''))
    ])
    return { file, direction, words }
  })
  const groups = {}
  for (const [group, tiers] of Object.entries(rules)) {
    for (const direction of ['C', 'L', 'R']) {
      const pools = tiers
        .map((keywords) =>
          candidates
            .filter(
              (item) =>
                (item.direction === direction || !item.direction) &&
                keywords.some((word) => item.words.has(word))
            )
            .map((item) => item.file)
        )
        .filter((pool) => pool.length)
      if (pools.length) groups[group + '_' + direction] = pools
    }
  }
  return groups
}

/**
 * 生成独立的 V3 文件分组索引。资源库可在首次读取旧模型时重建缓存。
 * @param {object} data 原始模型入口。
 * @param {string} [cacheRoot] 应用缓存目录。
 * @returns {Promise<Record<string, string[][]>>} 各方向分组按优先级排列的候选文件。
 */
export async function v3MotionGroups(data, cacheRoot) {
  const groups = data.FileReferences?.Motions ?? {}
  const grouped = Object.entries(groups)
    .filter(([group, values]) => !group.startsWith('__') && Array.isArray(values))
    .flatMap(([, values]) => values)
  const indexed = data.DSakiko?.MotionAssets
  const entries = Array.isArray(indexed) ? grouped.concat(indexed) : grouped
  const files = [
    ...new Set(entries.map((entry) => entry?.File).filter((file) => typeof file === 'string'))
  ].sort()
  if (!cacheRoot) return classify(files)
  const key = createHash('sha256').update(JSON.stringify(files)).digest('hex')
  const file = path.join(cacheRoot, 'live2d-motion-groups', 'v1', key + '.json')
  try {
    const index = JSON.parse(await readFile(file, 'utf8'))
    if (
      index &&
      typeof index === 'object' &&
      !Array.isArray(index) &&
      Object.values(index).every(
        (tiers) =>
          Array.isArray(tiers) &&
          tiers.every(
            (tier) => Array.isArray(tier) && tier.every((item) => typeof item === 'string')
          )
      )
    )
      return index
  } catch {
    // 缓存可被用户清除；原始模型始终足以重建索引。
  }
  const index = classify(files)
  await writeJson(file, index).catch(() => {})
  return index
}

/**
 * 在导入时预生成分组。未通过模型解析的包仍由原有资源流程诊断。
 * @param {string} entry 已复制的包入口文件。
 * @param {string} cacheRoot 应用缓存目录。
 * @param {AbortSignal} [signal] 取消本次导入。
 * @returns {Promise<void>} 可用索引生成完毕。
 */
export async function indexImportedV3Model(entry, cacheRoot, signal) {
  if (!entry.toLowerCase().endsWith('.json') || (await stat(entry)).size > 8 * 1024 * 1024) return
  const source = await readFile(entry, { encoding: 'utf8', signal })
  let model
  try {
    model = JSON.parse(source.replace(/^\uFEFF/u, ''))
  } catch {
    return
  }
  if (model?.FileReferences) await v3MotionGroups(model, cacheRoot)
}
