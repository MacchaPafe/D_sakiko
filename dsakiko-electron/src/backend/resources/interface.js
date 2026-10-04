/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

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
 * 资源模块：导入、下载、寻址和回收文件；对资源进行必要的格式、来源和访问校验。
 * 需要：文件来源、不透明的引用所有者编号、当前访问主体。
 * 不需要：Chat/Turn 结构、角色关系、文件将在哪一句播放、删除历史的业务判断。
 * 大型模型可使用受管理的外部资源目录，不要求复制进安装包或每份存档。
 * 持有者编号由草稿、存档和任务的负责模块生成并管理；引用关系在本模块内部保存，不是用户账号表。
 * 本模块负责应用自己的文件；厂商文件引用及远端删除由 RemoteFiles 管理。
 * 这是审查用接口，方法均未实现。
 */
export class MediaAssets {
  /**
   * 导入或下载资源，成功时立即挂到指定所有者，避免在交付结果前被回收。
   * 取消或失败不得留下可被误认为完成的资源；ownerId 可表示草稿、任务或存档，模块不解析其业务含义。
   * @param {AssetSource} source 来源。
   * @param {AssetOwnerId} ownerId 初始资源持有者；导入成功时添加引用，不替换该持有者已有的其他引用。
   * @param {AbortSignal} [signal] 取消导入。
   * @returns {Promise<AssetInfo>} 已就绪的资源信息。
   */
  async importAsset(source, ownerId, signal) {}

  /**
   * 读取资源元数据，不将本机路径暴露给页面。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<AssetInfo>} 资源信息；不存在时拒绝。
   */
  async inspect(asset) {}

  /**
   * 为访问主体解析有限期的可读取地址；桌面可用受限协议，手机可用鉴权 HTTP。
   * @param {AssetRef} asset 资源引用。
   * @param {string} accessSessionId 已授权的访问会话，不能由客户端自报权限。
   * @returns {Promise<{ url: string, expiresAt: string | null }>} 访问地址，不能当作持久化引用保存。
   */
  async resolveAccess(asset, accessSessionId) {}

  /**
   * 供后端能力读取资源；大文件实现时应使用流或受控本地访问，此处不开放给浏览器任意读盘。
   * @param {AssetRef} asset 资源引用。
   * @returns {Promise<Uint8Array>} 文件字节。
   */
  async read(asset) {}

  /**
   * 原子替换一个所有者持有的资源集合；传空数组表示释放该所有者的全部引用。
   * 交接资源时先建立新所有者引用，再释放旧引用；存档写入失败时不能先释放旧资源。
   * @param {AssetOwnerId} ownerId 资源持有者编号，不是 userId。
   * @param {AssetRef[]} assets 该所有者需要保留的全部资源。
   * @returns {Promise<void>} 引用已更新，不立即删除文件。
   */
  async setReferences(ownerId, assets) {}

  /**
   * 回收不再被存档、草稿或运行任务引用的受管理资源；不删除外部模型源文件。
   * @returns {Promise<{ removedCount: number, reclaimedBytes: number }>} 回收结果。
   */
  async collectUnused() {}
}
