import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { resolve, sep } from 'node:path'
import { Field, FixedSizeList, Float32, Int32, List, Schema, Utf8 } from 'apache-arrow'

const entryTypes = ['story_event', 'character_relation', 'lore_entry', 'character_thought']

/**
 * @typedef {Object} WorldbookRow
 * @property {string} entry_id 条目 ID。
 * @property {string} entry_type 条目类型。
 * @property {string} package_id 来源包。
 * @property {string} timeline_id 时间线。
 * @property {string} canon_branch 正史分支。
 * @property {number|null} visible_from 可见起点，包含边界。
 * @property {number|null} visible_to 可见终点，包含边界。
 * @property {string} character_id 观点所属角色。
 * @property {string} subject_character_id 关系主体。
 * @property {string} object_character_id 关系客体。
 * @property {string[]} known_by_character_ids 事件知情角色。
 * @property {string[]} tags 精确词加分来源。
 * @property {string} title 标题。
 * @property {string} text 与 Python 相同的 retrieval_text。
 * @property {string} content_json 完整正文 JSON。
 */

/**
 * @typedef {Object} WorldbookFilter
 * @property {string[]} packageIds 已解析的允许包集合。
 * @property {string} timelineId 时间线。
 * @property {string} canonBranch 正史分支。
 * @property {string} entryType 条目类型。
 * @property {number} queryTime 已编码的剧情时间。
 * @property {'active'|'started'|'none'} [timeMode] 时间过滤方式，默认 active。
 * @property {string} [subjectCharacterId] 观点所属角色或关系主体。
 * @property {string} [objectCharacterId] 关系客体。
 * @property {string} [knownByCharacterId] 事件知情角色。
 */

// 显式声明可空整数和列表，避免首条数据为 null 或空数组时推断出错误类型。
export const worldbookSchema = new Schema([
  ...['entry_id', 'entry_type', 'package_id', 'timeline_id', 'canon_branch',
    'character_id', 'subject_character_id', 'object_character_id', 'title', 'text',
    'content_json'].map(name => new Field(name, new Utf8(), false)),
  new Field('visible_from', new Int32(), true),
  new Field('visible_to', new Int32(), true),
  new Field('known_by_character_ids', new List(new Field('item', new Utf8(), false)), false),
  new Field('tags', new List(new Field('item', new Utf8(), false)), false),
  new Field('vector', new FixedSizeList(384, new Field('item', new Float32(), false)), false)
])

/**
 * 读取当前无依赖的官方 Schema v0 包，用作可复现导入示例。
 * @param {string} packageDirectory 含 manifest.json 的官方包目录。
 * @returns {Promise<WorldbookRow[]>} 四类条目的统一存储记录。
 */
export async function loadOfficialWorldbook(packageDirectory) {
  const root = resolve(packageDirectory)
  const manifest = JSON.parse(await readFile(resolve(root, 'manifest.json'), 'utf8'))
  if (manifest.format_version !== 1 || manifest.dependencies.length !== 0) {
    throw new Error('示例仅处理 format_version=1 的无依赖官方包；依赖与用户覆盖需由业务层先解析')
  }
  const rows = []
  const ids = new Set()
  for (const file of manifest.content_files) {
    const path = resolve(root, file.path)
    if (!path.startsWith(root + sep)) throw new Error('内容文件必须位于包目录内')
    const bytes = await readFile(path)
    if (createHash('sha256').update(bytes).digest('hex') !== file.sha256) {
      throw new Error(`内容校验失败：${file.path}`)
    }
    for (const entry of JSON.parse(bytes.toString('utf8')).entries) {
      const content = entry.content
      if (entry.schema_version !== 0 || entry.entry_type !== file.entry_type ||
          !entryTypes.includes(entry.entry_type) || !content.retrieval_text?.trim()) {
        throw new Error(`不支持的条目或缺少 retrieval_text：${entry.entry_id}`)
      }
      if (ids.has(entry.entry_id)) throw new Error(`重复条目：${entry.entry_id}`)
      ids.add(entry.entry_id)
      rows.push({
        entry_id: entry.entry_id, entry_type: entry.entry_type, package_id: manifest.package_id,
        timeline_id: content.timeline_id, canon_branch: content.canon_branch,
        visible_from: content.visible_from ?? null, visible_to: content.visible_to ?? null,
        character_id: content.character_id ?? '',
        subject_character_id: content.subject_character_id ?? '',
        object_character_id: content.object_character_id ?? '',
        known_by_character_ids: content.known_by_character_ids ?? [], tags: content.tags ?? [],
        title: content.title ?? '', text: content.retrieval_text,
        content_json: JSON.stringify(content)
      })
    }
  }
  return rows
}

/** @param {string} value SQL 字符串值。 @returns {string} 已转义的 SQL 字符串常量。 */
function sqlString(value) {
  if (typeof value !== 'string') throw new TypeError('过滤值必须为字符串')
  return `'${value.replaceAll("'", "''")}'`
}

/**
 * 把世界书硬约束转换成向量检索前的过滤条件。
 * @param {WorldbookFilter} filter 已解析的包、时间及角色约束。
 * @returns {string} LanceDB SQL 条件。
 */
export function worldbookWhere(filter) {
  if (!entryTypes.includes(filter.entryType) || !Number.isSafeInteger(filter.queryTime)) {
    throw new TypeError('条目类型或剧情时间无效')
  }
  if (!Array.isArray(filter.packageIds) || filter.packageIds.length === 0) {
    throw new TypeError('允许包集合不能为空')
  }
  const mode = filter.timeMode ?? 'active'
  if (!['active', 'started', 'none'].includes(mode)) throw new TypeError('时间过滤方式无效')
  const conditions = [
    `package_id IN (${filter.packageIds.map(sqlString).join(', ')})`,
    `timeline_id = ${sqlString(filter.timelineId)}`,
    `canon_branch = ${sqlString(filter.canonBranch)}`,
    `entry_type = ${sqlString(filter.entryType)}`
  ]
  if (mode !== 'none') {
    conditions.push(`(visible_from IS NULL OR visible_from <= ${filter.queryTime})`)
  }
  if (mode === 'active') {
    conditions.push(`(visible_to IS NULL OR visible_to >= ${filter.queryTime})`)
  }
  if (filter.subjectCharacterId !== undefined) {
    const field = filter.entryType === 'character_thought' ? 'character_id' : 'subject_character_id'
    conditions.push(`${field} = ${sqlString(filter.subjectCharacterId)}`)
  }
  if (filter.objectCharacterId !== undefined) {
    conditions.push(`object_character_id = ${sqlString(filter.objectCharacterId)}`)
  }
  if (filter.knownByCharacterId !== undefined) {
    if (filter.entryType !== 'story_event') throw new TypeError('知情角色仅用于事件检索')
    conditions.push(`array_has(known_by_character_ids, ${sqlString(filter.knownByCharacterId)})`)
  }
  return conditions.join(' AND ')
}

/**
 * 批量编码并创建持久化表；已有同名表会报错，避免覆盖现存数据。
 * @param {import('@lancedb/lancedb').Connection} db 本地数据库连接。
 * @param {import('./e5-embedding.mjs').E5Encoder} encoder 已加载的本地模型。
 * @param {WorldbookRow[]} rows 官方世界书记录。
 * @param {string} [tableName] 表名。
 * @returns {Promise<import('@lancedb/lancedb').Table>} 包含 384 维向量的表。
 */
export async function importWorldbook(db, encoder, rows, tableName = 'worldbook') {
  if (rows.length === 0) throw new Error('没有可导入条目')
  if ((await db.tableNames()).includes(tableName)) throw new Error(`表已存在：${tableName}`)
  const vectors = await encoder.encodePassages(rows.map(row => row.text))
  const records = rows.map((row, index) => ({ ...row, vector: vectors[index] }))
  return db.createTable(tableName, records, { schema: worldbookSchema })
}

/**
 * 在硬过滤范围内检索，先应用原始相似度阈值，再应用标题和标签加分。
 * @param {import('@lancedb/lancedb').Table} table 世界书表。
 * @param {number[]} vector 已归一化的 384 维查询向量。
 * @param {WorldbookFilter} filter 已解析的检索约束。
 * @param {{limit?: number, candidateLimit?: number, minScore?: number, boostSource?: string}} [options] 数量、阈值及加分来源。
 * @returns {Promise<Object[]>} 按最终分数排序的条目，score 为余弦相似度。
 */
export async function searchWorldbook(table, vector, filter, {
  limit = 5, candidateLimit = 24, minScore = -1, boostSource = ''
} = {}) {
  if (vector.length !== 384 || !vector.every(Number.isFinite)) throw new TypeError('查询向量必须为 384 维有限数值')
  if (!Number.isInteger(limit) || limit < 1 || !Number.isInteger(candidateLimit) || candidateLimit < limit ||
      !Number.isFinite(minScore) || minScore < -1 || minScore > 1) throw new RangeError('检索数量或阈值无效')
  // where 默认在 Top-K 前过滤；小规模世界书先用精确扫描，避免 ANN 参数影响召回。
  const rows = await table.vectorSearch(vector).distanceType('cosine')
    .where(worldbookWhere(filter)).bypassVectorIndex().limit(candidateLimit).toArray()
  const results = []
  const normalizedSource = boostSource.toLowerCase()
  for (const row of rows) {
    const score = 1 - row._distance
    if (score < minScore) continue
    let boost = 0
    if (normalizedSource && row.title && normalizedSource.includes(row.title.toLowerCase())) boost += 0.04
    if (normalizedSource && Array.from(row.tags).some(tag => tag && normalizedSource.includes(tag.toLowerCase()))) boost += 0.02
    boost = Math.min(boost, 0.05)
    results.push({
      entry_id: row.entry_id, entry_type: row.entry_type, title: row.title, text: row.text,
      score, boost, final_score: score + boost, content: JSON.parse(row.content_json)
    })
  }
  results.sort((left, right) => right.final_score - left.final_score ||
    (left.entry_id < right.entry_id ? -1 : Number(left.entry_id > right.entry_id)))
  return results.slice(0, limit)
}
