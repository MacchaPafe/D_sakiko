import { processUsesResources } from './process.js'
import { AssetCollector } from './collector.js'
import { extractAssetReferences } from './references.js'
import path from 'node:path'
import {
  readFile,
  writeFile,
  mkdir,
  rm,
  stat,
  lstat,
  realpath,
  copyFile,
  chmod
} from 'node:fs/promises'
import { containedPath, copyPackage, isWithin, resourcePath, validModelFilePath } from './files.js'
import {
  copy,
  requireValue,
  uuid,
  readJson,
  writeJson,
  SerialQueue,
  safeId,
  problem
} from '../core/support.js'

/** @typedef {import('../../shared/contracts/common.js').AssetRef} AssetRef */
/** @typedef {import('../../shared/contracts/assets.js').AssetPurpose} AssetPurpose */
/** @typedef {{root: 'application' | 'library' | 'cache', relativePath: string, entry: string | null, registered?: boolean}} AssetLocation 资源的内部存储位置。 */

const purpose_to_locations = {
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
 * @property {string} [libraryItemId] 用户保留的资源库条目编号，仅导入结果提供。
 * @property {number | null} durationMs 音视频时长（毫秒）；非音视频或未解析时为 null。
 */

/**
 * @typedef {object} AssetIndex
 * @property {number} version 索引格式版本。
 * @property {Record<string, {session: string, expiresAt: number | null, processId?: number, processGroupId?: number}>} temporaryOwners 本次运行的临时持有者与租约。
 * @property {Record<string, string>} commits 提交保护 owner 到正式 owner 的恢复日志。
 * @property {Record<string, AssetLocation>} locations 资源 ID 到内部位置的映射。
 * @property {Record<string, AssetInfo>} assets 资源 ID 到资源元数据的映射表。
 * @property {Record<string, string[]>} owners 资源持有者（如对话、草稿、任务）到其所持有资源 ID 列表的映射表，用于垃圾回收判断。
 */

/**
 * index.json：主要资源索引的 schema 如下：
 * {
      "version": 3, // 必须为 3
      "temporaryOwners": {},
      "commits": {},
      "assets": { // 记录所有 asset 的具体信息
        "9d5e38a2-1111-4222-8333-abcdef123456": {
          "asset": {
            "id": "9d5e38a2-1111-4222-8333-abcdef123456"
          },
          "purpose": "chat-image",  // AssetPurpose
          "kind": "file",  // "file" or "directory"
          "name": "screenshot.png",
          "mediaType": "image/png",  // standard http header
          "sizeBytes": 524288, // size of file. The whole directory size for directory.
          "durationMs": null // filled When kind == "audio"
        }
      },
      "locations": { // record the path of each asset
        "9d5e38a2-1111-4222-8333-abcdef123456": {
          "root": "application", // "application" | "library" | "cache"
          "relativePath": "files/9d5e38a2-1111-4222-8333-abcdef123456", // relative path under base root
          "entry": null // filled when kind == "directory", represent main file in directory, as "model3.json" for live2d model
        }
      },
      "owners": { // owner id for each asset
        "chat:e9c7a2b1-3333-4444-bbbb-ccccddddeeee": [
          "9d5e38a2-1111-4222-8333-abcdef123456"
        ]
      }
    }
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
    this.sessionId = uuid()
    this.committing = new SerialQueue()
    this.ownerReaders = new Map()
    this.releaseRetries = new Set()
    this.maintenanceProblems = []
    this.collector = new AssetCollector(this)
  }

  /**
   * 读取资源索引与持有者引用映射。
   * @returns {Promise<AssetIndex>} 索引状态对象。
   */
  async state() {
    const state = await readJson(this.index, {
      version: 3,
      assets: {},
      locations: {},
      owners: {},
      temporaryOwners: {},
      commits: {}
    })
    requireValue(
      state?.version === 3,
      'asset_layout_unsupported',
      '资源索引版本错误。这个问题不应当出现，请联系开发者。'
    )
    requireValue(
      [state.assets, state.locations, state.owners, state.temporaryOwners, state.commits].every(
        (value) => value && typeof value === 'object' && !Array.isArray(value)
      ),
      'invalid_asset_index'
    )
    for (const [id, info] of Object.entries(state.assets)) {
      safeId(id)
      const location = state.locations[id]
      requireValue(
        info?.asset?.id === id &&
          // 检查内容的 purpose（用途）属于内部定义好的
          Object.hasOwn(purpose_to_locations, info.purpose) &&
          ['file', 'directory'].includes(info.kind),
        'invalid_asset_index'
      )
      // 检查内容的存储位置是否符合规定，比如 live2d 类内容需要存储在 "library" 下
      requireValue(
        location && location.root === purpose_to_locations[info.purpose][0],
        'invalid_asset_index'
      )
      requireValue(
        // 确保实际存储路径符合规定前缀（如 "live2d"） + asset id 的样式
        location.registered === true
          ? info.purpose === 'model' &&
              info.kind === 'file' &&
              location.relativePath.startsWith('models/') &&
              validModelFilePath(location.relativePath.slice('models/'.length))
          : location.relativePath === purpose_to_locations[info.purpose][1] + '/' + id,
        'invalid_asset_index'
      )
      // 如果存储内容为目录，那么必须有一个 entry 标识主要文件，如 live2d 模型的 model3.json
      requireValue(info.kind === 'directory' || location.entry === null, 'invalid_asset_index')
      // 检查资源存储路径是否真的在规定路径中
      resourcePath(this.roots[location.root], location.relativePath)
      if (location.entry !== null) resourcePath('/package', location.entry)
    }
    requireValue(
      // 每一个 locations 中提到的 assets id 必须在 assets 中出现
      Object.keys(state.locations).every((id) => Object.hasOwn(state.assets, id)) &&
        // 每个在 owners 中被引用的 assets id 必须存在
        Object.values(state.owners).every(
          (refs) => Array.isArray(refs) && refs.every((id) => Object.hasOwn(state.assets, id))
        ),
      'invalid_asset_index'
    )
    for (const [owner, lease] of Object.entries(state.temporaryOwners)) {
      this.validateOwner(owner)
      requireValue(
        Object.hasOwn(state.owners, owner) &&
          typeof lease?.session === 'string' &&
          (lease.expiresAt === null || Number.isFinite(lease.expiresAt)) &&
          (lease.processId === undefined ||
            (Number.isInteger(lease.processId) && lease.processId > 0)) &&
          (lease.processGroupId === undefined ||
            (Number.isInteger(lease.processGroupId) && lease.processGroupId > 0)),
        'invalid_asset_index'
      )
    }
    for (const [pending, owner] of Object.entries(state.commits)) {
      this.validateOwner(owner)
      requireValue(
        pending.startsWith('pending:') && Object.hasOwn(state.owners, pending),
        'invalid_asset_index'
      )
    }
    return state
  }

  /**
   * 获取指定资源在本地磁盘上的绝对文件路径（仅供后端内部模块访问，不应当发送给前端）。
   * @param {AssetRef} asset 资源引用。
   * @param {string} [member] 如果 asset 是一个文件夹，需要额外给出文件夹内一个文件的相对路径，返回该文件的路径。
   * @returns {Promise<string>} 资源在磁盘上存储的绝对路径。
   */
  async localPath(asset, member) {
    const state = await this.state()
    requireValue(
      Object.hasOwn(state.assets, safeId(asset.id)) && !this.collector.blocked.has(asset.id),
      'asset_missing',
      '找不到要求的的资源。'
    )
    await this.collector.assertAvailable(state, [asset.id], false)
    const info = state.assets[safeId(asset.id)]
    requireValue(info, 'asset_missing', '找不到要求的的资源。')
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
   * 返回目录资源的入口相对路径，供模型目录定位同包标注；不公开资源库绝对路径。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<string | null>} 单文件资源返回 null。
   */
  async packageEntry(asset) {
    await this.inspect(asset)
    return (await this.state()).locations[asset.id].entry
  }

  /**
   * 导入一份媒体资源，并将其关联到指定的持有者名下。最大可以导入 64MB 的文件。
   * @param {AssetSource} source 资源数据来源（字节数组或本地文件路径）。
   * @param {AssetOwnerId} ownerId 初始持有者标识（例如草稿 ID、任务 ID 或对话 ID），用于防止资源在正式使用前被清理。
   * @param {AssetPurpose} purpose 资源用途。
   * @param {AbortSignal} [signal] 控制本次导入的取消信号。
   * @returns {Promise<AssetInfo>} 导入后的资源元数据信息。
   */
  async importAsset(source, ownerId, purpose, signal) {
    return this.queue.run('assets', async () => {
      signal?.throwIfAborted()
      this.validateOwner(ownerId)
      requireValue(Object.hasOwn(purpose_to_locations, purpose), 'invalid_asset_purpose')
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
        // 只允许 live2d 模型或其他模型文件使用文件夹形式导入
        requireValue(['model', 'live2d'].includes(purpose), 'invalid_asset_purpose')
        localDirectory = source.path
        name = path.basename(source.path)
        mediaType = 'application/octet-stream'
        if (purpose === 'live2d') {
          // live2d 模型必须有一个入口文件，即 model3.json 的描述文件
          requireValue(typeof source.entry === 'string', 'missing_model_entry')
        }
        if (source.entry) {
          const entry = resourcePath(source.path, source.entry)
          requireValue((await stat(entry)).isFile(), 'invalid_model_entry')
        }
      } else if (source.kind === 'remote-url') {
        // 禁止 remote-url 的直接导入，避免下载危险文件
        requireValue(false, 'unsupported_source', '远程 URL 不可以直接导入。')
      } else {
        requireValue(false, 'unsupported_source', `未知的资源类型 ${source.kind}`)
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
      const [root, folder] = purpose_to_locations[purpose]
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
   * 仅登记已经位于应用资源库中的基础模型文件，不复制、不移动、不接管旧项目目录。
   * @param {string[]} names 相对于 library/models 的基础资源文件路径。
   * @param {string} owner 登记期间的保护持有者。
   * @returns {Promise<Record<string, AssetRef>>} 路径到逐文件引用的映射。
   */
  async registerModelFiles(names, owner) {
    requireValue(
      Array.isArray(names) && names.every(validModelFilePath),
      'invalid_resource_manifest'
    )
    this.validateOwner(owner)
    return this.queue.run('assets', async () => {
      const state = await this.state()
      const root = await realpath(this.roots.library)
      const result = {}
      for (const name of new Set(names)) {
        const relativePath = 'models/' + name
        let file = root
        for (const part of relativePath.split('/')) {
          file = path.join(file, part)
          requireValue(!(await lstat(file)).isSymbolicLink(), 'invalid_asset_path')
        }
        const metadata = await stat(file)
        requireValue(metadata.isFile(), 'asset_not_file')
        const existing = Object.entries(state.locations).find(
          ([, location]) => location.root === 'library' && location.relativePath === relativePath
        )
        const id = existing ? existing[0] : uuid()
        requireValue(!this.collector.blocked.has(id), 'asset_unavailable')
        requireValue(
          !Object.values(state.locations).some(
            (location) =>
              location.root === 'library' && relativePath.startsWith(location.relativePath + '/')
          ),
          'overlapping_asset'
        )
        if (!existing) {
          state.assets[id] = {
            asset: { id },
            purpose: 'model',
            kind: 'file',
            name: path.basename(name),
            mediaType: 'application/octet-stream',
            sizeBytes: metadata.size,
            durationMs: null
          }
          state.locations[id] = { root: 'library', relativePath, entry: null, registered: true }
        }
        result[name] = { id }
      }
      state.owners[owner] = [
        ...new Set([...(state.owners[owner] ?? []), ...Object.values(result).map((ref) => ref.id)])
      ]
      await writeJson(this.index, state)
      return result
    })
  }

  /**
   * 查询指定资源的元数据信息。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<AssetInfo>} 资源元数据信息（资源不存在时抛出异常）。
   */
  async inspect(asset) {
    const state = await this.state()
    requireValue(
      Object.hasOwn(state.assets, safeId(asset.id)) && !this.collector.blocked.has(asset.id),
      'asset_missing',
      '资源不存在。'
    )
    await this.collector.assertAvailable(state, [asset.id], false)
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
    return this.using(asset, async () => {
      const file = await this.localPath(asset, member)
      requireValue((await stat(file)).isFile(), 'asset_not_file')
      return readFile(file)
    })
  }

  /**
   * 校验待持久保存的数据不依赖临时资源，允许保留已登记但暂时缺失的文件引用。
   * @param {unknown} value 待保存的业务数据。
   * @returns {Promise<void>}
   */
  async assertPersistent(value) {
    const state = await this.state()
    for (const ref of extractAssetReferences(value)) {
      const location = state.locations[safeId(ref.id)]
      requireValue(location && !this.collector.blocked.has(ref.id), 'asset_missing')
      requireValue(
        location.root !== 'cache',
        'temporary_asset_reference',
        '持久数据不能引用临时资源。'
      )
    }
  }

  /**
   * 增删或替换指定持有者名下的资源引用列表。
   * @param {AssetOwnerId} ownerId 资源持有者 ID。
   * @param {AssetRef[]} references 要增删或替换的资源引用列表。
   * @param {'add' | 'remove' | 'replace'} mode 操作方式：add 追加引用，remove 移除引用，replace 替换为新的引用列表。
   * @returns {Promise<void>}
   */
  async updateReferences(ownerId, references, mode, leaseTtl = null) {
    this.validateOwner(ownerId)
    const ids = references.map((asset) => safeId(asset.id))
    return this.queue.run('assets', async () => {
      const state = await this.state()
      if (leaseTtl !== null) this.refreshLease(state, ownerId, leaseTtl)
      if (mode !== 'remove') await this.collector.assertAvailable(state, ids)
      const current = new Set(state.owners[ownerId] ?? [])
      if (mode === 'add') ids.forEach((id) => current.add(id))
      if (mode === 'remove') ids.forEach((id) => current.delete(id))
      state.owners[ownerId] = mode === 'replace' ? [...new Set(ids)] : [...current]
      await writeJson(this.index, state)
    })
  }

  /**
   * 校验持有者 ID 是否合法。只检查对象类型，不会确定这个 ID 是否存在。
   * @param {string} ownerId 持有者 ID。
   */
  validateOwner(ownerId) {
    requireValue(
      typeof ownerId === 'string' &&
        ownerId.length > 0 &&
        ownerId.length <= 300 &&
        !['__proto__', 'constructor', 'prototype'].includes(ownerId),
      'invalid_owner'
    )
  }

  /**
   * 为指定的持有者追加资源引用。
   * @param {AssetOwnerId} ownerId 资源持有者 ID。
   * @param {AssetRef[]} references 要追加的资源引用列表。
   * @returns {Promise<void>}
   */
  async addReferences(ownerId, references) {
    return this.updateReferences(ownerId, references, 'add')
  }

  /**
   * 从指定的持有者名下移除部分资源引用。
   * @param {AssetOwnerId} ownerId 资源持有者 ID。
   * @param {AssetRef[]} references 要移除的资源引用列表。
   * @returns {Promise<void>}
   */
  async removeReferences(ownerId, references) {
    return this.updateReferences(ownerId, references, 'remove')
  }

  /**
   * 替换指定持有者名下的全部资源引用。
   * @param {AssetOwnerId} ownerId 资源持有者 ID。
   * @param {AssetRef[]} references 新的完整资源引用列表。
   * @returns {Promise<void>}
   */
  async replaceReferences(ownerId, references) {
    return this.updateReferences(ownerId, references, 'replace')
  }

  /**
   * 释放指定的资源持有者，清除其持有的所有资源引用。
   * @param {AssetOwnerId} ownerId 要释放的资源持有者 ID。
   * @returns {Promise<void>}
   */
  async releaseOwner(ownerId) {
    this.validateOwner(ownerId)
    await this.queue.run('assets', async () => {
      const state = await this.state()
      delete state.owners[ownerId]
      delete state.temporaryOwners[ownerId]
      await writeJson(this.index, state)
    })
    this.releaseRetries.delete(ownerId)
  }

  /**
   * 尝试释放指定的资源持有者；若释放失败则记录到待重试队列，避免因收尾清理失败而中断主业务流程。
   * @param {AssetOwnerId} ownerId 要释放的资源持有者 ID。
   * @returns {Promise<void>}
   */
  async releaseWhenPossible(ownerId) {
    try {
      await this.releaseOwner(ownerId)
    } catch (error) {
      this.releaseRetries.add(ownerId)
      this.reportMaintenance(error)
    }
  }

  /**
   * 记录后台资源维护过程中发生的异常。
   */
  reportMaintenance(error) {
    this.maintenanceProblems.push(problem(error))
    if (this.maintenanceProblems.length > 100) this.maintenanceProblems.shift()
  }

  /**
   * 创建一个临时的资源持有者，用于在草稿编辑、文件读取或任务执行期间保护相关资源不被清理。
   * @param {string} kind 临时持有者的用途前缀（如 'draft'、'use'、'scene'）。
   * @param {AssetRef[]} [references] 初始持有的资源引用列表。
   * @param {number | null} [ttlMs] 租约有效时长（毫秒）；如果不传或为 null，则不会按时间自动过期，需由调用方显式释放。
   * @returns {Promise<string>} 新创建的临时持有者 ID。
   */
  async createTemporaryOwner(kind, references = [], ttlMs = null) {
    safeId(kind)
    requireValue(ttlMs === null || (Number.isFinite(ttlMs) && ttlMs > 0), 'invalid_lease')
    const owner = kind + ':' + uuid()
    await this.queue.run('assets', async () => {
      const state = await this.state()
      const ids = references.map((asset) => safeId(asset.id))
      await this.collector.assertAvailable(state, ids)
      state.owners[owner] = [...new Set(ids)]
      state.temporaryOwners[owner] = {
        session: this.sessionId,
        expiresAt: ttlMs === null ? null : Date.now() + ttlMs
      }
      await writeJson(this.index, state)
    })
    return owner
  }

  /**
   * 将临时资源持有者与指定的子进程 PID 绑定，防止主进程重启后误释放仍在运行的子进程所使用的文件。
   * @param {string} owner 临时持有者 ID。
   * @param {number} [processId] 子进程 PID。
   * @returns {Promise<void>}
   */
  async bindOwnerProcess(owner, processId, processGroupId) {
    if (processId === undefined) return
    requireValue(
      Number.isInteger(processId) &&
        processId > 0 &&
        (processGroupId === undefined || (Number.isInteger(processGroupId) && processGroupId > 0)),
      'invalid_process'
    )
    await this.queue.run('assets', async () => {
      const state = await this.state()
      requireValue(state.temporaryOwners[owner], 'lease_expired')
      Object.assign(state.temporaryOwners[owner], { processId, processGroupId })
      await writeJson(this.index, state)
    })
  }

  /** 仅在进程树确已结束时释放；正常 Windows 退出可由宿主提供已确认的 PID。 */
  async releaseProcessOwner(owner, confirmedPid = null) {
    return this.queue.run('assets', async () => {
      const state = await this.state()
      const lease = state.temporaryOwners[owner]
      if (lease?.processId && lease.processId !== confirmedPid && processUsesResources(lease))
        return false
      delete state.owners[owner]
      delete state.temporaryOwners[owner]
      await writeJson(this.index, state)
      return true
    })
  }

  /**
   * 延长指定临时资源持有者的租约有效时间。
   * @param {string} owner 临时持有者 ID。
   * @param {number} ttlMs 顺延的有效时长（毫秒）。
   * @returns {Promise<void>}
   */
  async renewOwner(owner, ttlMs) {
    this.validateOwner(owner)
    requireValue(Number.isFinite(ttlMs) && ttlMs > 0, 'invalid_lease')
    await this.queue.run('assets', async () => {
      const state = await this.state()
      this.refreshLease(state, owner, ttlMs)
      await writeJson(this.index, state)
    })
  }

  /** 租约检查与引用变化必须在同一次资源队列操作内完成。 */
  refreshLease(state, owner, ttlMs) {
    const lease = state.temporaryOwners[owner]
    requireValue(
      lease &&
        lease.session === this.sessionId &&
        (lease.expiresAt === null || lease.expiresAt > Date.now()),
      'lease_expired'
    )
    lease.expiresAt = Date.now() + ttlMs
  }

  /** 从已提交目标的当前引用集合交接草稿，避免检查与释放之间目标再次改变。 */
  async consumeDraft(owner, targetOwner, ttlMs) {
    return this.queue.run('assets', async () => {
      const state = await this.state()
      if (!Object.hasOwn(state.owners, owner)) return { released: true }
      this.refreshLease(state, owner, ttlMs)
      requireValue(Object.hasOwn(state.owners, targetOwner), 'handoff_target_missing')
      const committed = new Set(state.owners[targetOwner])
      state.owners[owner] = state.owners[owner].filter((id) => !committed.has(id))
      const released = !state.owners[owner].length
      if (released) {
        delete state.owners[owner]
        delete state.temporaryOwners[owner]
      }
      await writeJson(this.index, state)
      return { released }
    })
  }

  /**
   * 在执行异步操作期间临时持有并保护给定数据中引用的所有资源，操作结束后自动释放。
   * @template T
   * @param {unknown} value 包含资源引用的数据对象。
   * @param {(owner: string) => Promise<T>} operation 需要执行的异步操作。
   * @returns {Promise<T>} 异步操作的返回结果。
   */
  async using(value, operation) {
    const owner = await this.createTemporaryOwner('use', extractAssetReferences(value))
    try {
      return await operation(owner)
    } finally {
      await this.releaseWhenPossible(owner)
    }
  }

  /**
   * 注册某类持久化业务对象（如对话、角色、全局设置）的读取函数，供中断恢复时重新核对磁盘上实际保存的资源引用。
   * @param {string} prefix 持有者类型前缀（如 'chat'、'character'、'settings'）。
   * @param {(id: string) => Promise<unknown>} reader 根据业务 ID 读取当前已保存数据的函数；若数据已删除则返回 null。
   * @returns {void}
   */
  registerOwnerReader(prefix, reader) {
    this.ownerReaders.set(prefix, reader)
  }

  /**
   * 读取指定持久化持有者当前在磁盘上已保存的数据。
   */
  async readCommitted(owner) {
    const separator = owner.indexOf(':')
    const reader = this.ownerReaders.get(owner.slice(0, separator))
    requireValue(reader, 'owner_reader_missing')
    return reader(owner.slice(separator + 1))
  }

  /**
   * 根据磁盘上已保存的实际业务数据，更新正式持有者的资源引用并清理对应的待提交日志。
   */
  async settleCommit(owner) {
    const value = await this.readCommitted(owner)
    const references = extractAssetReferences(value)
    await this.queue.run('assets', async () => {
      const state = await this.state()
      await this.collector.assertAvailable(
        state,
        references.map((ref) => ref.id)
      )
      if (value === null) delete state.owners[owner]
      else state.owners[owner] = references.map((ref) => ref.id)
      // 在同一次索引写入中同时更新正式引用并删除 pending 临时引用，防止中间状态导致资源短暂无人持有。
      for (const [pending, target] of Object.entries(state.commits)) {
        if (target !== owner) continue
        delete state.commits[pending]
        delete state.owners[pending]
      }
      await writeJson(this.index, state)
    })
  }

  /**
   * 安全地保存包含资源引用的业务数据，并在保存成功后同步更新该持有者的资源引用列表。
   * @template T
   * @param {string} owner 正式持有者 ID（如 'chat:xxx' 或 'settings:global'）。
   * @param {unknown} value 即将保存的完整业务数据；若为删除操作则传 null。
   * @param {() => Promise<T>} operation 实际写入业务文件的异步函数。
   * @returns {Promise<T>} 业务写入函数的返回结果。
   */
  async commitReferences(owner, value, operation) {
    this.validateOwner(owner)
    return this.committing.run(owner, async () => {
      await this.assertPersistent(value)
      const references = extractAssetReferences(value)
      const pending = 'pending:' + uuid()
      // 为什么在执行 operation 写盘之前先登记 pending 临时持有者：
      // 先在资源索引中锁定新数据引用的资源并记录提交日志，再写入业务文件；这样即使中途崩溃，重启后也能通过日志恢复引用，且新资源不会在业务写盘前被清理。
      await this.queue.run('assets', async () => {
        const state = await this.state()
        await this.collector.assertAvailable(
          state,
          references.map((ref) => ref.id)
        )
        state.owners[pending] = references.map((ref) => ref.id)
        state.commits[pending] = owner
        await writeJson(this.index, state)
      })
      let result
      try {
        result = await operation()
      } catch (error) {
        // 即使业务操作抛出异常，也可能已经完成了文件写入，因此仍以磁盘实际内容为准进行结算。
        await this.settleCommit(owner).catch((failure) => this.reportMaintenance(failure))
        throw error
      }
      await this.settleCommit(owner).catch((error) => this.reportMaintenance(error))
      return result
    })
  }

  /**
   * 初始化资源管理器，恢复上次未完成的引用提交并清理上一次进程遗留的临时持有者。
   * @returns {Promise<void>}
   */
  async initialize() {
    const state = await this.state()
    for (const owner of new Set(Object.values(state.commits))) await this.settleCommit(owner)
    await this.queue.run('assets', async () => {
      const next = await this.state()
      for (const [owner, lease] of Object.entries(next.temporaryOwners)) {
        if (lease.session === this.sessionId || processUsesResources(lease)) continue
        delete next.owners[owner]
        delete next.temporaryOwners[owner]
      }
      await writeJson(this.index, next)
    })
    const recovery = await this.collector.collect({}, true)
    for (const failure of recovery.failed) this.reportMaintenance({ problem: failure.problem })
    requireValue(!recovery.aborted, 'resource_recovery_failed')
  }

  /**
   * 执行定期维护，清理已过期的临时租约并重试之前失败的引用释放操作。
   * @returns {Promise<void>}
   */
  async maintain() {
    const pending = await this.state()
    for (const owner of new Set(Object.values(pending.commits))) {
      await this.committing
        .run(owner, async () => {
          const current = await this.state()
          if (Object.values(current.commits).includes(owner)) await this.settleCommit(owner)
        })
        .catch((error) => this.reportMaintenance(error))
    }
    for (const owner of [...this.releaseRetries]) await this.releaseWhenPossible(owner)
    await this.queue.run('assets', async () => {
      const state = await this.state()
      let changed = false
      for (const [owner, lease] of Object.entries(state.temporaryOwners)) {
        const processEnded = lease.processId && !processUsesResources(lease)
        const expired = lease.expiresAt !== null && lease.expiresAt <= Date.now()
        if (!processEnded && !expired) continue
        delete state.owners[owner]
        delete state.temporaryOwners[owner]
        changed = true
      }
      if (changed) await writeJson(this.index, state)
    })
  }

  /**
   * 即时回收零引用资源，将其删除。
   * @param {{dryRun?: boolean, resources?: AssetRef[]}} [options] 预览或限定候选。
   * @returns {Promise<import('./collector.js').CollectionResult>} 清理结果与单项错误。
   */
  async collectUnused(options) {
    return this.collector.collect(options)
  }
}
