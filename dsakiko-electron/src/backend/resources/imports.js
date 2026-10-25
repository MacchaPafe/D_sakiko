import { SerialQueue, requireValue, safeId, uuid } from '../core/support.js'

/** 草稿租约的有效时长（30 分钟）。编辑页面存活期间会定期续期；若页面异常关闭，超过该时间后草稿引用会自动过期。 */
const DRAFT_TTL = 30 * 60 * 1000

/**
 * 资源草稿与资源库导入管理模块，负责管理编辑过程中的临时资源草稿、长期资源库条目以及草稿保存后的引用交接。
 */
export class ResourceImports {
  /**
   * 创建一个资源导入与草稿管理器。
   * @param {import('./interface.js').MediaAssets} assets 媒体资源管理器。
   */
  constructor(assets) {
    this.assets = assets
    this.queue = new SerialQueue()
  }

  /**
   * 创建一个新的资源草稿，用于在编辑角色、设置或发送消息前暂存刚导入的附件。
   * @returns {Promise<{draftId: string}>} 新创建的草稿 ID。
   */
  async createDraft(references = []) {
    const owner = await this.assets.createTemporaryOwner('draft', references, DRAFT_TTL)
    return { draftId: owner.slice('draft:'.length) }
  }

  /**
   * 校验草稿是否仍然有效，并顺延其有效时间。
   */
  async owner(draftId) {
    const owner = 'draft:' + safeId(draftId)
    await this.assets.renewOwner(owner, DRAFT_TTL)
    return owner
  }

  /**
   * 为指定的资源草稿续期，延长其保留时间。
   * @param {string} draftId 草稿 ID。
   * @returns {Promise<void>}
   */
  async renewDraft(draftId) {
    await this.queue.run(draftId, () => this.owner(draftId))
  }

  /**
   * 放弃指定的资源草稿，并释放该草稿对资源的引用。
   * @param {string} draftId 草稿 ID。
   * @returns {Promise<void>}
   */
  async discardDraft(draftId) {
    await this.queue.run(draftId, () => this.assets.releaseOwner('draft:' + safeId(draftId)))
  }

  /**
   * 编辑已有资源时，把选中的资源加入有效草稿；过期身份必须显式重建。
   * @param {string} draftId 草稿编号。
   * @param {import('../../shared/contracts/common.js').AssetRef[]} references 需要保留的资源。
   * @returns {Promise<void>}
   */
  async addDraftAssets(draftId, references) {
    return this.queue.run(draftId, async () => {
      await this.assets.updateReferences('draft:' + safeId(draftId), references, 'add', DRAFT_TTL)
    })
  }

  /**
   * 从草稿中移除指定的附件资源引用。
   * @param {string} draftId 草稿 ID。
   * @param {import('../../shared/contracts/common.js').AssetRef[]} references 要从草稿中移除的资源引用列表。
   * @returns {Promise<void>}
   */
  async removeDraftAssets(draftId, references) {
    await this.queue.run(draftId, async () => {
      await this.assets.updateReferences(
        'draft:' + safeId(draftId),
        references,
        'remove',
        DRAFT_TTL
      )
    })
  }

  /**
   * 导入一份媒体资源。如果不传入草稿 ID，则仅允许导入模型资源并直接存入长期资源库；传入草稿 ID 时，资源会关联到该草稿名下。
   * @param {import('./interface.js').AssetSource} source 资源数据来源（字节数据或本地文件/目录路径）。
   * @param {import('../../shared/contracts/assets.js').AssetPurpose} purpose 资源用途。
   * @param {string | null} [draftId] 关联的草稿 ID；省略或为 null 时直接导入到长期资源库。
   * @returns {Promise<import('./interface.js').AssetInfo>} 导入后的资源元数据信息。
   */
  async importAsset(source, purpose, draftId = null) {
    requireValue(purpose !== 'temporary', 'invalid_asset_purpose')
    if (draftId !== null) {
      return this.queue.run(draftId, async () => {
        const owner = await this.owner(draftId)
        // 为什么复制期间要先创建独立的临时持有者 importing：
        // 大体积模型文件复制耗时较长，可能超出草稿单次租期；用独立的临时持有者保护复制过程，并在复制完成后再次刷新草稿租期，防止复制完瞬间因租约过期丢失引用。
        const importing = await this.assets.createTemporaryOwner('import')
        try {
          const info = await this.assets.importAsset(source, importing, purpose)
          await this.owner(draftId)
          await this.assets.updateReferences(owner, [info.asset], 'add', DRAFT_TTL)
          if (['model', 'live2d'].includes(purpose)) {
            const itemId = uuid()
            await this.assets.addReferences('library:' + itemId, [info.asset])
            return { ...info, libraryItemId: itemId }
          }
          return info
        } finally {
          await this.assets.releaseWhenPossible(importing)
        }
      })
    }
    requireValue(['model', 'live2d'].includes(purpose), 'draft_required', '请先创建资源草稿。')
    const owner = 'library:' + uuid()
    const info = await this.assets.importAsset(source, owner, purpose)
    return { ...info, libraryItemId: owner.slice('library:'.length) }
  }

  /**
   * 列出长期资源库中保留的所有条目及其关联的资源信息。
   * @returns {Promise<Array<{itemId: string, assets: import('./interface.js').AssetInfo[]}>>} 资源库条目列表。
   */
  async listLibraryItems() {
    const state = await this.assets.state()
    return Object.entries(state.owners)
      .filter(([owner]) => owner.startsWith('library:'))
      .map(([owner, ids]) => ({
        itemId: owner.slice('library:'.length),
        assets: ids.map((id) => state.assets[id])
      }))
  }

  /**
   * 从长期资源库中移除指定的条目。
   * @param {string} itemId 资源库条目 ID。
   * @returns {Promise<void>}
   */
  async removeLibraryItem(itemId) {
    await this.assets.releaseOwner('library:' + safeId(itemId))
  }

  /**
   * 在目标业务数据（如对话、角色或设置）保存成功后，提交草稿并清理已被目标接管的草稿资源引用。
   * @param {string} draftId 草稿 ID。
   * @param {{kind: 'chat' | 'character' | 'persona' | 'settings', id: string}} target 已保存的目标业务类型与 ID。
   * @returns {Promise<{released: boolean}>} 若草稿中的所有资源都已交接完毕并释放了草稿，则 released 为 true。
   */
  async commitDraft(draftId, target) {
    requireValue(['chat', 'character', 'persona', 'settings'].includes(target?.kind))
    const targetOwner = target.kind + ':' + safeId(target.id)
    return this.queue.run(draftId, () =>
      this.assets.consumeDraft('draft:' + safeId(draftId), targetOwner, DRAFT_TTL)
    )
  }
}
