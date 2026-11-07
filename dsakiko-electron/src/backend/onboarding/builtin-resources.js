import path from 'node:path'
import { mkdir, readdir, rename, rm, stat } from 'node:fs/promises'
import { readJson, writeJson, requireValue, safeId, SerialQueue } from '../core/support.js'
import { resourcePath } from '../resources/files.js'
import { extractArchive } from '../setup/installers.js'

/**
 * 内置模型的按需解压缓存；预览与正式导入共用文件，但预览不会写入角色目录。
 * 同一缓存队列覆盖解压、读取和回收，避免初始化与预热同时使用半成品。
 */
export class BuiltinResources {
  constructor({ cacheRoot }) {
    this.root = path.join(cacheRoot, 'builtin-characters')
    this.queue = new SerialQueue()
    this.previews = new Set()
    this.closed = false
  }

  /**
   * 在模型可读期间执行操作，导入用临时副本用完即删，预览保留到退出。
   * @template T
   * @param {string} source 内置包根目录。
   * @param {import('./interface.js').BuiltinManifest} manifest 当前资源清单。
   * @param {import('./interface.js').BuiltinCharacter} role 角色包。
   * @param {import('./interface.js').BuiltinForm} form 要释放的形态。
   * @param {(directory: string) => Promise<T>} operation 使用解压目录的操作。
   * @param {boolean} [preview] 是否保留本次运行的预览文件。
   * @returns {Promise<T>} 操作结果。
   */
  usingForm(source, manifest, role, form, operation, preview = false) {
    requireValue(!this.closed, 'onboarding_closed')
    return this.queue.run('cache', async () => {
      // 开发来源和旧测试清单仍可使用目录；正式包只携带归档。
      if (!role.archive) return operation(resourcePath(source, form.directory))
      const version = safeId(manifest.contentVersion)
      const root = path.join(this.root, version)
      await mkdir(root, { recursive: true })
      for (const name of await readdir(this.root)) {
        if (name !== version) await rm(path.join(this.root, name), { recursive: true, force: true })
      }
      const destination = path.join(root, safeId(role.id), safeId(form.id))
      const directory = resourcePath(destination, form.directory)
      const marker = path.join(destination, 'ready.json')
      let ready = false
      try {
        const state = await readJson(marker)
        ready =
          state?.contentVersion === version &&
          (await stat(resourcePath(directory, form.entry))).isFile()
      } catch (error) {
        if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error
      }
      if (!ready) {
        const staging = destination + '.staging'
        await rm(staging, { recursive: true, force: true })
        await rm(destination, { recursive: true, force: true })
        try {
          requireValue(
            Number.isSafeInteger(role.expandedBytes) && role.expandedBytes > 0,
            'invalid_builtin_archive'
          )
          await extractArchive(
            resourcePath(source, role.archive),
            staging,
            'tar.gz',
            role.expandedBytes,
            undefined,
            (name) => name === form.directory || name.startsWith(form.directory + '/')
          )
          requireValue(
            (await stat(resourcePath(resourcePath(staging, form.directory), form.entry))).isFile(),
            'builtin_resources_missing'
          )
          await writeJson(path.join(staging, 'ready.json'), { contentVersion: version })
          await rename(staging, destination)
        } finally {
          await rm(staging, { recursive: true, force: true })
        }
      }
      if (preview) this.previews.add(destination)
      try {
        return await operation(directory)
      } finally {
        if (!this.previews.has(destination)) await rm(destination, { recursive: true, force: true })
      }
    })
  }

  /** 已初始化的启动或退出可清理缓存；窗口仍在预览时不删除其文件。 */
  clear() {
    return this.queue.run('cache', async () => {
      this.previews.clear()
      await rm(this.root, { recursive: true, force: true })
    })
  }

  async close(initialized) {
    this.closed = true
    if (initialized) await this.clear()
    else await this.queue.run('cache', () => {})
  }
}
