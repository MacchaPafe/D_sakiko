import path from 'node:path'
import { lstat, realpath, rename, mkdir, rm, readdir } from 'node:fs/promises'
import { SerialQueue, requireValue, safeId, writeJson, problem } from '../core/support.js'

/**
 * @typedef {object} CollectionResult
 * @property {import('./interface.js').AssetInfo[]} candidates 本轮检查时无引用的候选。
 * @property {{id: string}[]} deleted 已删除文件并提交索引的资源。
 * @property {{id: string}[]} missing 已确认缺失并清除索引的资源。
 * @property {{id: string}[]} orphaned 隔离区内已无索引的残留，大小未知；全量预览也会列出。
 * @property {Array<{asset: {id: string}, reason: string}>} skipped 有引用或不存在的指定资源。
 * @property {Array<{asset: {id: string} | null, problem: object}>} failed 单项失败及索引故障。
 * @property {number} estimatedBytes 候选登记的逻辑大小，不代表实际磁盘释放量。
 * @property {number} deletedLogicalBytes 本轮已清除文件的登记大小。
 * @property {boolean} aborted 索引故障或未恢复提交导致本轮停止。
 */

/** 只处理登记资源；目录隔离到同盘专用目录，不扫描其他磁盘文件。 */
export class AssetCollector {
  constructor(assets) {
    this.assets = assets
    this.queue = new SerialQueue()
    this.blocked = new Set()
  }

  /** 用独立方法提供故障注入位置，业务调用方不接触文件系统。 */
  async persist(state) {
    await writeJson(this.assets.index, state)
  }
  async remove(target) {
    await rm(target, { recursive: true, force: false })
  }

  /** 根目录和受管父目录必须可访问；拒绝通过内部符号链接删除其他位置。 */
  async paths(state, id) {
    const location = state.locations[id]
    const root = await realpath(this.assets.roots[location.root])
    let current = root
    for (const part of path.dirname(location.relativePath).split('/')) {
      current = path.join(current, part)
      const directory = await this.metadata(current)
      if (!directory) break
      requireValue(directory.isDirectory() && !directory.isSymbolicLink(), 'invalid_asset_path')
    }
    const staging = path.join(root, '.asset-gc')
    const stagedDirectory = await this.metadata(staging)
    requireValue(
      !stagedDirectory || (stagedDirectory.isDirectory() && !stagedDirectory.isSymbolicLink()),
      'invalid_asset_path'
    )
    return {
      source: path.join(root, location.relativePath),
      staging,
      staged: path.join(staging, id)
    }
  }

  /** 只把明确的 ENOENT 视为不存在，权限或设备故障继续上抛。 */
  async metadata(file) {
    try {
      return await lstat(file)
    } catch (error) {
      if (error.code === 'ENOENT') return null
      throw error
    }
  }

  referenced(state, id) {
    return Object.values(state.owners).some((ids) => ids.includes(id))
  }

  /**
   * 引用接纳与隔离核查共用资源队列；离线的零引用资源不能在未恢复前重新接管。
   * 已有持有者的缺失资源仍允许保存原引用，不改变既有离线数据策略。
   */
  async assertAvailable(state, ids, acquire = true) {
    for (const id of new Set(ids)) {
      requireValue(state.assets[id] && !this.blocked.has(id), 'asset_missing')
      let paths
      try {
        paths = await this.paths(state, id)
      } catch (error) {
        if (error.problem || (acquire && !this.referenced(state, id)))
          throw error.problem
            ? error
            : Object.assign(error, {
                problem: {
                  code: 'asset_unavailable',
                  message: '资源所在目录尚未完成恢复。',
                  retryable: true
                }
              })
        continue
      }
      if (await this.metadata(paths.staged)) {
        this.blocked.add(id)
        requireValue(false, 'asset_missing', '资源已进入清理隔离区，不能重新使用。')
      }
      if (acquire && !this.referenced(state, id)) {
        const source = await this.metadata(paths.source)
        requireValue(source && !source.isSymbolicLink(), 'asset_unavailable')
      }
    }
  }

  /** 删除和提交索引共用资源队列；提交失败时隔离 ID，禁止从旧索引重新建立引用。 */
  async item(id, result, dryRun, missingOnly) {
    return this.assets.queue.run('assets', async () => {
      const state = await this.assets.state()
      if (!Object.hasOwn(state.assets, id)) {
        result.skipped.push({
          asset: { id },
          reason: 'not_registered'
        })
        return
      }
      const info = state.assets[id]
      let absent
      try {
        const paths = await this.paths(state, id)
        const source = await this.metadata(paths.source)
        const staged = await this.metadata(paths.staged)
        if (this.referenced(state, id) && !staged) {
          result.skipped.push({ asset: { id }, reason: 'referenced' })
          return
        }
        result.candidates.push(info)
        result.estimatedBytes += info.sizeBytes
        if (dryRun) return
        requireValue(!source?.isSymbolicLink() && !staged?.isSymbolicLink(), 'invalid_asset_path')
        requireValue(
          !source || (info.kind === 'directory' ? source.isDirectory() : source.isFile()),
          'invalid_asset_path'
        )
        requireValue(
          !staged || (staged.isDirectory() && info.kind === 'directory' && !source),
          'invalid_asset_path'
        )
        if (staged) this.blocked.add(id)
        if (missingOnly && (source || staged)) return
        absent = !source && !staged
        if (source?.isDirectory()) {
          await mkdir(paths.staging, { recursive: true })
          await rename(paths.source, paths.staged)
          this.blocked.add(id)
          await this.remove(paths.staged)
        } else if (staged) {
          await this.remove(paths.staged)
        } else if (source) {
          await this.remove(paths.source)
        }
        this.blocked.add(id)
      } catch (error) {
        result.failed.push({ asset: { id }, problem: problem(error) })
        return
      }
      delete state.assets[id]
      delete state.locations[id]
      // 隔离目录已不再是可用资源；旧错误状态中的索引引用也不能让它复活。
      for (const [owner, ids] of Object.entries(state.owners)) {
        if (ids.includes(id)) {
          state.owners[owner] = ids.filter((value) => value !== id)
          this.assets.reportMaintenance({
            problem: {
              code: 'quarantined_asset_reference',
              message: '已移除指向隔离资源的失效索引引用。',
              retryable: false
            }
          })
        }
      }
      try {
        await this.persist(state)
      } catch (error) {
        result.aborted = true
        result.failed.push({ asset: { id }, problem: problem(error) })
        return
      }
      this.blocked.delete(id)
      if (absent) result.missing.push({ id })
      else {
        result.deleted.push({ id })
        result.deletedLogicalBytes += info.sizeBytes
      }
    })
  }

  /** 仅显式全量清理处理隔离区的无索引残留，不扫描其他未登记目录。 */
  async collectOrphans(result, dryRun) {
    await this.assets.queue.run('assets', async () => {
      const state = await this.assets.state()
      for (const root of Object.values(this.assets.roots)) {
        let staging
        let entries
        try {
          staging = path.join(await realpath(root), '.asset-gc')
          const metadata = await this.metadata(staging)
          if (!metadata) continue
          requireValue(metadata.isDirectory() && !metadata.isSymbolicLink(), 'invalid_asset_path')
          entries = await readdir(staging)
        } catch (error) {
          if (error.code !== 'ENOENT') result.failed.push({ asset: null, problem: problem(error) })
          continue
        }
        for (const id of entries) {
          try {
            safeId(id)
            if (Object.hasOwn(state.assets, id)) continue
            const target = path.join(staging, id)
            requireValue(!(await lstat(target)).isSymbolicLink(), 'invalid_asset_path')
            result.orphaned.push({ id })
            if (dryRun) continue
            await this.remove(target)
            result.deleted.push({ id })
          } catch (error) {
            result.failed.push({ asset: null, problem: problem(error) })
          }
        }
      }
    })
  }

  /**
   * 执行或预览一次清理，指定 resources 时只处理这些 ID。
   * missingOnly 仅供启动恢复，不删除任何现存资源或隔离目录。
   * @param {{dryRun?: boolean, resources?: Array<{id: string}>}} options 调用选项。
   * @param {boolean} [missingOnly] 是否仅移除缺失且零引用的索引。
   * @returns {Promise<CollectionResult>} 逐项执行结果。
   */
  async collect(options = {}, missingOnly = false) {
    requireValue(options && typeof options === 'object' && !Array.isArray(options))
    requireValue(Object.keys(options).every((key) => ['dryRun', 'resources'].includes(key)))
    const { dryRun = false, resources } = options
    requireValue(
      typeof dryRun === 'boolean' && (resources === undefined || Array.isArray(resources))
    )
    const selected = resources?.map((ref) => safeId(ref.id))
    return this.queue.run('collection', async () => {
      const result = {
        candidates: [],
        deleted: [],
        missing: [],
        orphaned: [],
        skipped: [],
        failed: [],
        estimatedBytes: 0,
        deletedLogicalBytes: 0,
        aborted: false
      }
      if (!dryRun && !missingOnly) await this.assets.maintain()
      const state = await this.assets.state()
      if (Object.keys(state.commits).length && !dryRun) {
        result.aborted = true
        result.failed.push({
          asset: null,
          problem: {
            code: 'resource_recovery_pending',
            message: '资源提交尚未恢复，请稍后重试。',
            retryable: true
          }
        })
        return result
      }
      const ids = selected ?? Object.keys(state.assets)
      for (const id of new Set(ids)) {
        await this.item(id, result, dryRun, missingOnly)
        if (result.aborted) break
      }
      if (!result.aborted && !missingOnly && selected === undefined)
        await this.collectOrphans(result, dryRun)
      return result
    })
  }
}
