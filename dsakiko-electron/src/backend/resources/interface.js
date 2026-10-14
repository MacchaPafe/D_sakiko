import path from 'node:path'
import { readFile, writeFile, mkdir, unlink, stat, copyFile, chmod } from 'node:fs/promises'
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
/** @typedef {import('../../shared/contracts/common.js').AssetOwnerId} AssetOwnerId */

/**
 * 本地导入路径只能由可信宿主提供；远程客户端上传字节，不能让后端读取任意路径。
 * @typedef {{ kind: 'bytes', name: string, mediaType: string, bytes: Uint8Array }
 *   | { kind: 'local-file', path: string }
 *   | { kind: 'remote-url', url: string }} AssetSource
 */

/**
 * @typedef {object} AssetInfo
 * @property {AssetRef} asset 稳定资源引用。
 * @property {string} name 展示名。
 * @property {string} mediaType 媒体类型。
 * @property {number} sizeBytes 文件长度。
 * @property {number | null} durationMs 可解析的音频或视频时长；其他资源为 null。
 */

/**
 * @typedef {object} AssetIndex
 * @property {Record<string, AssetInfo>} assets 已发布的资源元数据。
 * @property {Record<string, string[]>} owners 不透明所有者到资源身份的引用集合，决定资源是否可回收。
 */

/**
 * 资源模块：导入、下载、寻址和回收文件；对资源进行必要的格式、来源和访问校验。
 * 需要：文件来源、不透明的引用所有者编号、当前访问主体。
 * 不需要：Chat/Turn 结构、角色关系、文件将在哪一句播放、删除历史的业务判断。
 * 大型模型可使用受管理的外部资源目录，不要求复制进安装包或每份存档。
 * 持有者编号由草稿、存档和任务的负责模块生成并管理；引用关系在本模块内部保存，不是用户账号表。
 * 本模块负责应用自己的文件；厂商文件引用及远端删除由 RemoteFiles 管理。
 * 业务实现由宿主注入存储位置与外部能力。
 */
export class MediaAssets {
  /**
   * 绑定受管理资源目录与可选的访问授权器。
   * @param {object} options 宿主依赖。
   * @param {string} options.directory 资源文件及索引的存储目录。
   * @param {((asset: AssetRef, sessionId: string) => Promise<{url: string, expiresAt: string | null}>) | null} [options.grantAccess] 已授权会话的短期读取地址解析能力。
   */
  constructor({ directory, grantAccess = null }) {
    this.directory = directory
    this.index = path.join(directory, 'index.json')
    this.queue = new SerialQueue()
    this.grantAccess = grantAccess
  }
  /** 读取资源与持有者的同一份索引快照。
   * @returns {Promise<AssetIndex>} 修改必须在 assets 串行队列内进行。
   */
  async state() {
    return readJson(this.index, { assets: {}, owners: {} })
  }
  /**
   * 计算可信后端使用的资源路径；公开传输只交付资源引用和短期 URL。
   * @param {AssetRef} asset 资源身份；调用前须通过 inspect 确认存在。
   * @returns {string} 私有绝对路径，不进入 HTTP 路由表。
   */
  localPath(asset) {
    return path.join(this.directory, safeId(asset.id))
  }

  /**
   * 导入或下载资源，成功时立即挂到指定所有者，避免在交付结果前被回收。
   * 取消或失败不得留下可被误认为完成的资源；ownerId 可表示草稿、任务或存档，模块不解析其业务含义。
   * @param {AssetSource} source 来源。
   * @param {AssetOwnerId} ownerId 初始资源持有者；导入成功时添加引用，不替换该持有者已有的其他引用。
   * @param {AbortSignal} [signal] 取消导入。
   * @returns {Promise<AssetInfo>} 已就绪的资源信息。
   */
  async importAsset(source, ownerId, signal) {
    return this.queue.run('assets', async () => {
      signal?.throwIfAborted()
      requireValue(typeof ownerId === 'string' && ownerId.length > 0)
      let bytes
      let name
      let mediaType
      let localFile = null
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
        name: String(name),
        mediaType: String(mediaType),
        sizeBytes,
        durationMs: null
      }
      await mkdir(this.directory, { recursive: true })
      // 权重可很大，本机导入直接复制文件，不把全部内容读入 Node 堆。
      if (localFile) {
        await copyFile(localFile, this.localPath(asset))
        await chmod(this.localPath(asset), 0o600)
      } else await writeFile(this.localPath(asset), bytes, { flag: 'wx', mode: 0o600, signal })
      try {
        const state = await this.state()
        state.assets[asset.id] = info
        state.owners[ownerId] = [...new Set([...(state.owners[ownerId] ?? []), asset.id])]
        await writeJson(this.index, state)
      } catch (error) {
        await unlink(this.localPath(asset)).catch(() => {})
        throw error
      }
      return copy(info)
    })
  }

  /**
   * 读取资源元数据，不将本机路径暴露给页面。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<AssetInfo>} 资源信息；不存在时拒绝。
   */
  async inspect(asset) {
    const info = (await this.state()).assets[asset.id]
    requireValue(info, 'asset_missing', '资源不存在。')
    return copy(info)
  }

  /**
   * 为访问主体解析有限期的可读取地址；桌面可用受限协议，手机可用鉴权 HTTP。
   * @param {AssetRef} asset 资源引用。
   * @param {string} accessSessionId 已授权的访问会话，不能由客户端自报权限。
   * @returns {Promise<{ url: string, expiresAt: string | null }>} 访问地址，不能当作持久化引用保存。
   */
  async resolveAccess(asset, accessSessionId) {
    await this.inspect(asset)
    requireValue(this.grantAccess, 'adapter_unavailable')
    return this.grantAccess(asset, accessSessionId)
  }

  /**
   * 供后端能力读取资源；大文件实现时应使用流或受控本地访问，此处不开放给浏览器任意读盘。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<Uint8Array>} 文件字节。
   */
  async read(asset) {
    await this.inspect(asset)
    return readFile(this.localPath(asset))
  }

  /**
   * 原子替换一个所有者持有的资源集合；传空数组表示释放该所有者的全部引用。
   * 交接资源时先建立新所有者引用，再释放旧引用；存档写入失败时不能先释放旧资源。
   * @param {AssetOwnerId} ownerId 资源持有者编号，不是 userId。
   * @param {AssetRef[]} assets 该所有者需要保留的全部资源。
   * @returns {Promise<void>} 引用已更新，不立即删除文件。
   */
  async setReferences(ownerId, assets) {
    return this.queue.run('assets', async () => {
      const state = await this.state()
      requireValue(
        assets.every((asset) => state.assets[asset.id]),
        'asset_missing'
      )
      state.owners[ownerId] = [...new Set(assets.map((asset) => asset.id))]
      await writeJson(this.index, state)
    })
  }

  /**
   * 回收不再被存档、草稿或运行任务引用的受管理资源；不删除外部模型源文件。
   * @returns {Promise<{ removedCount: number, reclaimedBytes: number }>} 回收结果。
   */
  async collectUnused() {
    return this.queue.run('assets', async () => {
      const state = await this.state()
      const held = new Set(Object.values(state.owners).flat())
      let removedCount = 0
      let reclaimedBytes = 0
      for (const [id, info] of Object.entries(state.assets)) {
        if (held.has(id)) continue
        await unlink(this.localPath({ id })).catch((error) => {
          if (error.code !== 'ENOENT') throw error
        })
        delete state.assets[id]
        removedCount += 1
        reclaimedBytes += info.sizeBytes
      }
      await writeJson(this.index, state)
      return { removedCount, reclaimedBytes }
    })
  }
}
