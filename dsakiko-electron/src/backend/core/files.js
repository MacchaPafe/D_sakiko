import { mkdir, readFile, rename, writeFile, unlink } from 'node:fs/promises'
import { dirname } from 'node:path'
import { randomUUID } from 'node:crypto'
import { problemError } from '../../shared/problems.js'

/** 按身份串行处理修改，失败不会阻塞下一次操作。 */
export class SerialQueue {
  tails = new Map()
  /**
   * 将同一身份的读改写作为一个操作排队；不同身份不互相阻塞。
   * @template T
   * @param {string} key 串行化身份；共享索引应使用同一个键。
   * @param {() => T | Promise<T>} operation 轮到执行时才读取最新状态。
   * @returns {Promise<T>} 本次操作结果；失败仍向调用方传播。
   */
  run(key, operation) {
    const next = (this.tails.get(key) || Promise.resolve()).then(operation)
    // 队尾吞掉失败仅用于放行下一次操作，调用方仍拿到会拒绝的 next。
    const settled = next.catch(() => {})
    this.tails.set(key, settled)
    void settled.then(() => {
      // 旧操作收尾时不能删掉已被后续操作替换的队尾。
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
    // 临时文件与目标同目录，使读者只会看到替换前或替换后的完整 JSON。
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
