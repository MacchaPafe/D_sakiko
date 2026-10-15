import path from 'node:path'
import { readFile, writeFile, mkdir, rm, stat, copyFile, chmod } from 'node:fs/promises'
import { containedPath, copyPackage, isWithin, resourcePath } from './files.js'
import {
  copy,
  requireValue,
  uuid,
  readJson,
  writeJson,
  SerialQueue,
  safeId
} from '../core/support.js'

/** @typedef {import('../../shared/contracts/common.js').AssetRef} AssetRef */
/** @typedef {import('../../shared/contracts/assets.js').AssetPurpose} AssetPurpose */
/** @typedef {{root: 'application' | 'library' | 'cache', relativePath: string, entry: string | null}} AssetLocation 资源的内部存储位置。 */

const locations = {
  'chat-image': ['application', 'files'],
  avatar: ['application', 'files'],
  attachment: ['application', 'files'],
  model: ['library', 'models'],
  live2d: ['library', 'live2d'],
  audio: ['library', 'audio'],
  archive: ['cache', 'assets'],
  temporary: ['cache', 'assets']
}
/** @typedef {import('../../shared/contracts/common.js').AssetOwnerId} AssetOwnerId */

/**
 * 资源导入来源（支持二进制字节、本地文件路径或远程 URL）。
 * @typedef {{ kind: 'bytes', name: string, mediaType: string, bytes: Uint8Array }
 *   | { kind: 'local-file', path: string }
 *   | { kind: 'local-directory', path: string, entry?: string }
 *   | { kind: 'remote-url', url: string }} AssetSource
 */

/**
 * @typedef {object} AssetInfo
 * @property {AssetRef} asset 资源引用标识。
 * @property {AssetPurpose} purpose 资源用途。
 * @property {'file' | 'directory'} kind 文件或完整目录。
 * @property {string} name 文件展示名称。
 * @property {string} mediaType MIME 媒体类型。
 * @property {number} sizeBytes 文件字节大小。
 * @property {number | null} durationMs 音视频时长（毫秒）；非音视频或未解析时为 null。
 */

/**
 * @typedef {object} AssetIndex
 * @property {number} version 索引格式版本。
 * @property {Record<string, AssetLocation>} locations 资源 ID 到内部位置的映射。
 * @property {Record<string, AssetInfo>} assets 资源 ID 到资源元数据的映射表。
 * @property {Record<string, string[]>} owners 资源持有者（如对话、草稿、任务）到其所持有资源 ID 列表的映射表，用于垃圾回收判断。
 */

/**
 * 本地资源管理模块，统一管理图片、音频和模型的导入、读取与持有关系。
 */
export class MediaAssets {
  /**
   * 创建一个媒体资源管理器。
   * @param {object} options 创建选项。
   * @param {string} options.appDataRoot 应用数据根目录，保存统一索引和聊天图片。
   * @param {string} options.resourceLibraryRoot 模型与保留音频的根目录。
   * @param {string} options.cacheRoot 临时资源根目录。
   * @param {((asset: AssetRef, sessionId: string) => Promise<{url: string, expiresAt: string | null}>) | null} [options.grantAccess] 自定义函数，用于为指定会话生成资源的临时访问 URL。
   */
  constructor({ appDataRoot, resourceLibraryRoot, cacheRoot, grantAccess = null }) {
    for (const root of [appDataRoot, resourceLibraryRoot, cacheRoot])
      requireValue(
        typeof root === 'string' && path.isAbsolute(root),
        'invalid_data_root',
        '数据目录必须是绝对路径。'
      )
    this.roots = {
      application: path.join(appDataRoot, 'assets'),
      library: resourceLibraryRoot,
      cache: cacheRoot
    }
    const roots = [appDataRoot, resourceLibraryRoot, cacheRoot]
    requireValue(
      roots.every((root, index) =>
        roots.every(
          (other, otherIndex) =>
            index === otherIndex || (!isWithin(root, other) && !isWithin(other, root))
        )
      ),
      'overlapping_data_roots',
      '三个数据目录不能重叠。'
    )
    this.index = path.join(this.roots.application, 'index.json')
    this.queue = new SerialQueue()
    this.grantAccess = grantAccess
  }

  /**
   * 读取资源索引与持有者引用映射。
   * @returns {Promise<AssetIndex>} 索引状态对象。
   */
  async state() {
    const state = await readJson(this.index, { version: 2, assets: {}, locations: {}, owners: {} })
    requireValue(
      state?.version === 2,
      'asset_layout_unsupported',
      '资源索引不是多目录格式；请保留原数据并另行处理兼容，不会自动迁移。'
    )
    requireValue(
      [state.assets, state.locations, state.owners].every(
        (value) => value && typeof value === 'object' && !Array.isArray(value)
      ),
      'invalid_asset_index'
    )
    for (const [id, info] of Object.entries(state.assets)) {
      safeId(id)
      const location = state.locations[id]
      requireValue(
        info?.asset?.id === id &&
          Object.hasOwn(locations, info.purpose) &&
          ['file', 'directory'].includes(info.kind),
        'invalid_asset_index'
      )
      requireValue(location && location.root === locations[info.purpose][0], 'invalid_asset_index')
      requireValue(
        location.relativePath === locations[info.purpose][1] + '/' + id,
        'invalid_asset_index'
      )
      requireValue(info.kind === 'directory' || location.entry === null, 'invalid_asset_index')
      resourcePath(this.roots[location.root], location.relativePath)
      if (location.entry !== null) resourcePath('/package', location.entry)
    }
    requireValue(
      Object.keys(state.locations).every((id) => Object.hasOwn(state.assets, id)) &&
        Object.values(state.owners).every(
          (refs) => Array.isArray(refs) && refs.every((id) => Object.hasOwn(state.assets, id))
        ),
      'invalid_asset_index'
    )
    return state
  }

  /**
   * 获取指定资源在本地磁盘上的绝对文件路径（仅供后端内部模块访问）。
   * @param {AssetRef} asset 资源引用。
   * @param {string} [member] 模型包内的相对文件路径。
   * @returns {Promise<string>} 已校验的本地路径。
   */
  async localPath(asset, member) {
    const state = await this.state()
    requireValue(Object.hasOwn(state.assets, safeId(asset.id)), 'asset_missing', '资源未登记。')
    const info = state.assets[safeId(asset.id)]
    requireValue(info, 'asset_missing', '资源未登记。')
    const location = state.locations[asset.id]
    const root = this.roots[location.root]
    const base = resourcePath(root, location.relativePath)
    try {
      await containedPath(root, base)
      const relative = member ?? location.entry
      if (relative !== null && relative !== undefined) {
        requireValue(info.kind === 'directory', 'invalid_asset_path')
        const target = resourcePath(base, relative)
        return await containedPath(base, target)
      }
      return base
    } catch (error) {
      if (['ENOENT', 'ENOTDIR', 'EACCES'].includes(error.code))
        requireValue(false, 'asset_unavailable', '资源文件或所在目录不可用。')
      throw error
    }
  }

  /**
   * 导入一份媒体资源，并将其关联到指定的持有者名下。
   * @param {AssetSource} source 资源数据来源（字节数组或本地文件路径）。
   * @param {AssetOwnerId} ownerId 初始持有者标识（例如草稿 ID、任务 ID 或对话 ID），用于防止资源在正式使用前被清理。
   * @param {AssetPurpose} purpose 资源用途，由调用操作明确指定。
   * @param {AbortSignal} [signal] 控制本次导入的取消信号。
   * @returns {Promise<AssetInfo>} 导入后的资源元数据信息。
   */
  async importAsset(source, ownerId, purpose, signal) {
    return this.queue.run('assets', async () => {
      signal?.throwIfAborted()
      requireValue(typeof ownerId === 'string' && ownerId.length > 0)
      requireValue(Object.hasOwn(locations, purpose), 'invalid_asset_purpose')
      const state = await this.state()
      let bytes
      let name
      let mediaType
      let localFile = null
      let localDirectory = null
      let sizeBytes = 0
      if (source.kind === 'bytes') {
        bytes = new Uint8Array(source.bytes)
        name = source.name
        mediaType = source.mediaType
      } else if (source.kind === 'local-file') {
        localFile = source.path
        const metadata = await stat(localFile)
        requireValue(metadata.isFile())
        sizeBytes = metadata.size
        name = path.basename(source.path)
        mediaType =
          {
            '.wav': 'audio/wav',
            '.mp3': 'audio/mpeg',
            '.ogg': 'audio/ogg',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.json': 'application/json'
          }[path.extname(name).toLowerCase()] ?? 'application/octet-stream'
      } else if (source.kind === 'local-directory') {
        requireValue(['model', 'live2d'].includes(purpose), 'invalid_asset_purpose')
        localDirectory = source.path
        name = path.basename(source.path)
        mediaType = 'application/octet-stream'
        if (purpose === 'live2d')
          requireValue(typeof source.entry === 'string', 'missing_model_entry')
        if (source.entry) {
          const entry = resourcePath(source.path, source.entry)
          requireValue((await stat(entry)).isFile(), 'invalid_model_entry')
        }
      } else {
        requireValue(false, 'unsupported_source', '远程 URL 需经受控下载适配器导入。')
      }
      if (bytes) {
        requireValue(bytes.byteLength <= 64 * 1024 * 1024, 'asset_too_large')
        sizeBytes = bytes.byteLength
      }
      signal?.throwIfAborted()
      const asset = { id: uuid() }
      const info = {
        asset,
        purpose,
        kind: localDirectory ? 'directory' : 'file',
        name: String(name),
        mediaType: String(mediaType),
        sizeBytes,
        durationMs: null
      }
      const [root, folder] = locations[purpose]
      const location = {
        root,
        relativePath: folder + '/' + asset.id,
        entry: localDirectory ? (source.entry ?? null) : null
      }
      const destination = resourcePath(this.roots[root], location.relativePath)
      await mkdir(path.dirname(destination), { recursive: true })
      const parent = await containedPath(this.roots[root], path.dirname(destination))
      if (localDirectory) {
        const sourceRoot = await containedPath(localDirectory, localDirectory)
        requireValue(
          !isWithin(sourceRoot, path.join(parent, asset.id)),
          'invalid_asset_package',
          '模型源目录不能包含导入目标。'
        )
      }
      try {
        // 大文件直接复制，避免将完整模型读入 Node.js 内存。
        if (localDirectory) info.sizeBytes = await copyPackage(localDirectory, destination, signal)
        else if (localFile) {
          await copyFile(localFile, destination)
          await chmod(destination, 0o600)
        } else await writeFile(destination, bytes, { flag: 'wx', mode: 0o600, signal })
        signal?.throwIfAborted()
        state.assets[asset.id] = info
        state.locations[asset.id] = location
        state.owners[ownerId] = [...new Set([...(state.owners[ownerId] ?? []), asset.id])]
        await writeJson(this.index, state)
      } catch (error) {
        await rm(destination, { recursive: true, force: true }).catch(() => {})
        throw error
      }
      return copy(info)
    })
  }

  /**
   * 查询指定资源的元数据信息。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<AssetInfo>} 资源元数据信息（资源不存在时抛出异常）。
   */
  async inspect(asset) {
    const state = await this.state()
    requireValue(Object.hasOwn(state.assets, safeId(asset.id)), 'asset_missing', '资源不存在。')
    const info = state.assets[asset.id]
    return copy(info)
  }

  /**
   * 为指定访问会话生成该资源的临时可读取 URL。
   * @param {AssetRef} asset 资源引用。
   * @param {string} accessSessionId 已授权的访问会话 ID。
   * @returns {Promise<{ url: string, expiresAt: string | null }>} 资源的访问 URL 及过期时间。
   */
  async resolveAccess(asset, accessSessionId) {
    await this.inspect(asset)
    requireValue(this.grantAccess, 'adapter_unavailable')
    return this.grantAccess(asset, accessSessionId)
  }

  /**
   * 读取指定资源的原始文件字节内容。
   * @param {AssetRef} asset 资源引用。
   * @param {string} [member] 模型包内的相对文件路径。
   * @returns {Promise<Uint8Array>} 文件字节数据。
   */
  async read(asset, member) {
    const file = await this.localPath(asset, member)
    requireValue((await stat(file)).isFile(), 'asset_not_file')
    return readFile(file)
  }

  /**
   * 校验待持久保存的数据不依赖临时资源，允许保留已登记但暂时缺失的文件引用。
   * @param {unknown} value 待保存的业务数据。
   * @returns {Promise<void>}
   */
  async assertPersistent(value) {
    const state = await this.state()
    function visit(item) {
      if (!item || typeof item !== 'object') return
      if (!Array.isArray(item) && Object.keys(item).length === 1 && typeof item.id === 'string') {
        const location = state.locations[safeId(item.id)]
        requireValue(location, 'asset_missing')
        requireValue(
          location.root !== 'cache',
          'temporary_asset_reference',
          '持久数据不能引用临时资源。'
        )
      } else Object.values(item).forEach(visit)
    }
    visit(value)
  }

  /**
   * 更新指定持有者所持有的完整资源列表（传空数组表示释放该持有者的所有资源引用）。
   * @param {AssetOwnerId} ownerId 资源持有者标识。
   * @param {AssetRef[]} assets 该持有者当前需要保留的全部资源引用列表。
   * @returns {Promise<void>}
   */
  async setReferences(ownerId, assets) {
    return this.queue.run('assets', async () => {
      const state = await this.state()
      requireValue(
        assets.every((asset) => Object.hasOwn(state.assets, safeId(asset.id))),
        'asset_missing'
      )
      state.owners[ownerId] = [...new Set(assets.map((asset) => asset.id))]
      await writeJson(this.index, state)
    })
  }

  /**
   * 资源清理暂未开放。
   * @returns {Promise<void>}
   */
  async collectUnused() {
    requireValue(false, 'cleanup_unavailable', '资源清理暂未开放。')
  }
}
