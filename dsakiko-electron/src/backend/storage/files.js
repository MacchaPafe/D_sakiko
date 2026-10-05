import { mkdir, readFile, rename, writeFile, unlink, readdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { problemError } from '../../shared/contracts/desktop.js'

/** 按身份串行处理修改，失败不会阻塞下一次操作。 */
export class SerialQueue {
  tails = new Map()
  run(key, operation) {
    const next = (this.tails.get(key) || Promise.resolve()).then(operation)
    const settled = next.catch(() => {})
    this.tails.set(key, settled)
    void settled.then(() => {
      if (this.tails.get(key) === settled) this.tails.delete(key)
    })
    return next
  }
}

/**
 * 读取可选 JSON；损坏文件必须报告，不能伪装成首次启动。
 * @param {string} path 文件路径。
 * @param {object|null} [fallback] 不存在时的默认数据。
 * @returns {Promise<object|null>} JSON 数据。
 */
export async function readJson(path, fallback = null) {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch (error) {
    if (error.code === 'ENOENT') return fallback
    throw error
  }
}

/**
 * 在同一目录原子替换文件，限制配置与聊天的默认访问权限。
 * @param {string} path 文件路径。
 * @param {object} value 可序列化内容。
 * @returns {Promise<void>} 完整内容已发布。
 */
export async function writeJson(path, value) {
  await mkdir(dirname(path), { recursive: true })
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(value, null, 2), { mode: 0o600 })
    await rename(temporary, path)
  } finally {
    await unlink(temporary).catch(() => {})
  }
}

/**
 * 校验用于文件名的稳定身份。
 * @param {string} id 身份。
 * @returns {string} 已校验身份。
 */
export function safeId(id) {
  if (typeof id !== 'string' || !/^[\p{L}\p{N}_-]{1,100}$/u.test(id))
    throw problemError('invalid_id', '编号无效。')
  return id
}

/** 带版本的 JSON 存档；同一身份的比较与发布串行完成。 */
export class JsonStore {
  queue = new SerialQueue()
  constructor(directory) {
    this.directory = directory
  }
  path(id) {
    return join(this.directory, `${safeId(id)}.json`)
  }
  async load(id) {
    const envelope = await readJson(this.path(id))
    if (!envelope) return null
    if (envelope.schemaVersion !== 1 || !envelope.data || envelope.data.id !== id)
      throw problemError('invalid_archive', '存档格式不受支持。')
    return envelope.data
  }
  async list() {
    await mkdir(this.directory, { recursive: true })
    const names = (await readdir(this.directory)).filter((name) => name.endsWith('.json'))
    return Promise.all(names.map((name) => this.load(name.slice(0, -5))))
  }
  save(snapshot) {
    const input = structuredClone(snapshot)
    return this.queue.run(input.id, async () => {
      const previous = await this.load(input.id)
      if (
        !Number.isInteger(input.revision) ||
        input.revision < 0 ||
        (previous?.revision || 0) !== input.revision
      )
        throw problemError('revision_conflict', '内容已经改变，请重新读取后再保存。')
      const saved = { ...input, revision: input.revision + 1 }
      await writeJson(this.path(input.id), { schemaVersion: 1, data: saved })
      return structuredClone(saved)
    })
  }
  delete(id, expectedRevision) {
    return this.queue.run(id, async () => {
      const previous = await this.load(id)
      if (previous?.revision !== expectedRevision)
        throw problemError('revision_conflict', '内容已经改变，请刷新后再删除。')
      await unlink(this.path(id))
    })
  }
}
