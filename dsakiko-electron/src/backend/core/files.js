import { mkdir, readFile, rename, writeFile, unlink } from 'node:fs/promises'
import { dirname } from 'node:path'
import { randomUUID } from 'node:crypto'
import { problemError } from '../../shared/problems.js'

/**
 * 按分组键（key）串行执行异步操作，避免并发修改同一份数据。
 * 这相当于一个细粒度锁：所有以同一个 key 运行的任务都在使用同一把锁，串行运行。
 */
export class SerialQueue {
  tails = new Map()
  /**
   * 将操作加入指定键的队列末尾，等待同一个键之前的操作完成后再执行。
   * @template T
   * @param {string} key 队列分组标识；不同 key 之间的操作可以同时进行，相同 key 的多个操作需要排队完成。
   * @param {() => T | Promise<T>} operation 要执行的回调函数。
   * @returns {Promise<T>} 操作的执行结果。
   */
  run(key, operation) {
    const next = (this.tails.get(key) || Promise.resolve()).then(operation)
    // 队尾使用 catch 忽略错误仅为了不阻塞后续排队任务，调用方拿到的 next 仍会正常抛出错误。
    const settled = next.catch(() => {})
    this.tails.set(key, settled)
    void settled.then(() => {
      // 仅当自身仍是该队列的最后一个任务时才清理 Map，避免误删后续新排入的任务。
      if (this.tails.get(key) === settled) this.tails.delete(key)
    })
    return next
  }
}

/**
 * 读取并解析 JSON 文件。
 * @param {string} path 文件路径。
 * @param {object | null} [fallback] 文件不存在时返回的默认值。
 * @returns {Promise<object | null>} 解析后的 JSON 对象。
 */
export async function readJson(path, fallback = null) {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch (error) {
    // 仅在文件不存在时返回默认值；如果文件已存在但 JSON 格式损坏，必须抛出异常而非当作新文件覆盖。
    if (error.code === 'ENOENT') return fallback
    throw error
  }
}

/**
 * 将对象安全地序列化并写入 JSON 文件。
 * @param {string} path 目标文件路径。
 * @param {object} value 要写入的可序列化对象。
 * @returns {Promise<void>}
 */
export async function writeJson(path, value) {
  await mkdir(dirname(path), { recursive: true })
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(value, null, 2), { mode: 0o600 })
    // 先写入同目录临时文件再通过 rename 原子替换，防止写入中途进程崩溃留下半截损坏的 JSON 文件。
    await rename(temporary, path)
  } finally {
    await unlink(temporary).catch(() => {})
  }
}

/**
 * 校验 ID 是否可安全用作文件名。
 * 
 * 校验标准与安全设计：
 * 1. 字符白名单：仅允许 Unicode 文字字符（\p{L}，支持中英日韩等各语言字母与汉字）、
 *    Unicode 数字（\p{N}）、下划线（_）与连字符（-）。
 * 2. 长度限制：1 至 100 个字符，防止超长文件名溢出文件系统上限（通常为 255 字节），并留出扩展名拼接空间。
 * 3. 安全防御设计：禁止包含路径分隔符（/ 与 \）和点号（.），从而禁止父级/同级路径穿越（如 ../xxx 的写法）
 * @param {string} id 待校验的 ID 字符串。
 * @returns {string} 校验通过的原 ID（类型不符或不满足白名单标准时抛出 invalid_id 异常）。
 */
export function safeId(id) {
  if (typeof id !== 'string' || !/^[\p{L}\p{N}_-]{1,100}$/u.test(id))
    throw problemError('invalid_id', '编号无效。')
  return id
}
