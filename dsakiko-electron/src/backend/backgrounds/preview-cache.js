import { lstat, mkdir, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { SerialQueue, uuid } from '../core/support.js'

export const previewImageLimit = 4 * 1024 * 1024
export const backgroundPreviewCacheDirectory = 'background-previews'
const capacityBytes = 100_000_000
const maxAgeMs = 7 * 24 * 60 * 60 * 1000
const cacheName = /^[a-f0-9]{64}\.png$/
const temporaryName = /^[a-f0-9]{64}\.png\.[a-f0-9-]{36}\.tmp$/

/** @param {string} name 缓存目录下的文件名。 @returns {boolean} 是否为本模块生成的文件。 */
export function isBackgroundPreviewCacheFile(name) {
  return cacheName.test(name) || temporaryName.test(name)
}

/** 仅保存在线图库按需获取的 PNG，写入排队保证并行请求共用同一容量预算。 */
export class BackgroundPreviewCache {
  queue = new SerialQueue()
  entries = new Map()
  size = 0
  initialized = false
  generation = 0

  constructor(directory, capacity = capacityBytes) {
    this.directory = directory
    this.capacity = capacity
  }

  /** @param {string} name 包含资源版本的缓存文件名。 @returns {Promise<Buffer|null>} 仍有效的 PNG 字节。 */
  async read(name) {
    if (!(await lstat(this.directory)).isDirectory()) return null
    const file = join(this.directory, name)
    const entry = await lstat(file)
    if (!entry.isFile() || entry.size > previewImageLimit || Date.now() - entry.mtimeMs >= maxAgeMs)
      return null
    return readFile(file)
  }

  async files() {
    try {
      if (!(await lstat(this.directory)).isDirectory()) return []
      const files = await readdir(this.directory, { withFileTypes: true })
      return files
        .filter((file) => file.isFile() && isBackgroundPreviewCacheFile(file.name))
        .map(({ name }) => name)
    } catch (error) {
      if (error.code === 'ENOENT') return []
      throw error
    }
  }

  async initialize() {
    const entries = []
    for (const name of await this.files()) {
      const location = join(this.directory, name)
      if (temporaryName.test(name)) {
        await rm(location, { force: true })
      } else {
        const { size, mtimeMs } = await lstat(location)
        entries.push({ name, size, mtimeMs })
      }
    }
    entries.sort((a, b) => a.mtimeMs - b.mtimeMs)
    this.entries = new Map(entries.map(({ name, size }) => [name, size]))
    this.size = entries.reduce((sum, entry) => sum + entry.size, 0)
    this.initialized = true
  }

  async remove(name) {
    await rm(join(this.directory, name), { force: true })
    this.size -= this.entries.get(name)
    this.entries.delete(name)
  }

  /**
   * @param {string} name 包含资源版本的缓存文件名。
   * @param {Buffer} bytes 已验证的 PNG。
   * @param {number} [generation] 加载图片时的缓存代次。
   * @returns {Promise<void>} 原子发布图片并记入容量。
   */
  async write(name, bytes, generation = this.generation) {
    if (bytes.length > this.capacity) return
    await this.queue.run('write', async () => {
      if (generation !== this.generation) return
      await mkdir(this.directory, { recursive: true, mode: 0o700 })
      if (!(await lstat(this.directory)).isDirectory()) return
      if (!this.initialized) await this.initialize()
      if (this.entries.has(name)) await this.remove(name)
      while (this.size + bytes.length > this.capacity) {
        await this.remove(this.entries.keys().next().value)
      }
      const file = join(this.directory, name)
      const temporary = file + '.' + uuid() + '.tmp'
      try {
        // 先腾出空间，再原子发布，避免并发写入或进程中断留下可被读到的半张图。
        await writeFile(temporary, bytes, { flag: 'wx', mode: 0o600 })
        await rename(temporary, file)
      } finally {
        await rm(temporary, { force: true }).catch(() => {})
      }
      this.entries.set(name, bytes.length)
      this.size += bytes.length
    })
  }

  /**
   * @template T
   * @param {boolean} remove 是否删除所列文件。
   * @param {(names: string[]) => Promise<T>} operation 存储管理对文件执行的操作。
   * @returns {Promise<T>} 预览或清理结果。
   */
  withFiles(remove, operation) {
    return this.queue.run('write', async () => {
      if (remove) {
        // 清理后重新扫描剩余文件，同时避免清理前已经开始的网络请求重新填回磁盘。
        this.generation += 1
        this.initialized = false
      }
      return operation(await this.files())
    })
  }
}
