import path from 'node:path'
import { DownloadJobFeed } from '../core/download-job-feed.js'
import {
  SerialQueue,
  SnapshotBus,
  problem,
  readJson,
  requireValue,
  uuid,
  writeJson
} from '../core/support.js'
import { BackgroundSource, imageType } from './source.js'

/** @typedef {{asset: import('../../shared/contracts/common.js').AssetRef, name: string, filename: string, key: string|null}} BackgroundEntry 可独立于对话移除的图库条目。 */
/** @typedef {{id: string, kind: 'background', source: import('./source.js').OnlineBackground, status: string, progress?: {completed: number,total: number}, completedAt?: number, problem?: object}} BackgroundJob 背景图片下载任务。 */

function empty() {
  return { items: [], jobs: [], removed: [] }
}

/** 全局背景图库。图库、对话与访问租约分别持有资源，任务完成与原图入库一次提交。 */
export class Backgrounds {
  constructor({ appDataRoot, assets, fetch, token }) {
    this.file = path.join(appDataRoot, 'backgrounds.json')
    this.assets = assets
    this.source = new BackgroundSource(fetch, token)
    this.queue = new SerialQueue()
    this.events = new SnapshotBus()
    this.downloadJobs = new DownloadJobFeed()
    this.tasks = new Map()
    assets.registerOwnerReader(
      'backgrounds',
      async () => (await readJson(this.file, empty())).items
    )
  }

  async initialize() {
    this.data = await readJson(this.file, empty())
    for (const job of this.data.jobs) {
      if (['running', 'installing'].includes(job.status)) job.status = 'interrupted'
      this.downloadJobs.publish(job)
    }
    if (this.data.jobs.some((job) => job.status === 'interrupted'))
      await writeJson(this.file, this.data)
  }

  async change(edit, gallery = false) {
    return this.queue.run('state', async () => {
      const next = structuredClone(this.data)
      const result = await edit(next)
      const save = () => writeJson(this.file, next)
      if (gallery) await this.assets.commitReferences('backgrounds:gallery', next.items, save)
      else await save()
      this.data = next
      if (gallery) this.events.emit('items', next.items)
      if (result?.status) this.downloadJobs.publish(result)
      return result
    })
  }

  observe(listener) {
    return this.events.subscribe('items', listener, this.data.items)
  }
  catalog(refresh) {
    return this.source.catalog(refresh)
  }
  preview(key) {
    return this.source.preview(key)
  }

  async importImages(refs) {
    requireValue(Array.isArray(refs) && refs.length > 0)
    const items = []
    for (const ref of refs) {
      const info = await this.assets.inspect(ref)
      requireValue(
        info.purpose === 'chat-image' && info.kind === 'file' && info.sizeBytes <= 64 * 1024 * 1024,
        'invalid_image',
        '请选择 PNG、JPEG 或 WebP 图片。'
      )
      requireValue(
        imageType(await this.assets.read(ref)) === info.mediaType,
        'invalid_image',
        '图片内容与文件格式不符。'
      )
      items.push({
        asset: info.asset,
        name: path.parse(info.name).name,
        filename: info.name,
        key: null
      })
    }
    await this.change((data) => {
      for (const item of items)
        if (!data.items.some((row) => row.asset.id === item.asset.id)) data.items.push(item)
    }, true)
  }

  async rename(id, name) {
    requireValue(
      typeof name === 'string' && name.trim() && name.trim().length <= 160,
      'invalid_name',
      '请输入 1 至 160 字的名称。'
    )
    await this.change((data) => {
      const item = data.items.find((row) => row.asset.id === id)
      requireValue(item, 'background_missing', '图片已从图库移除。')
      item.name = name.trim()
    }, true)
  }

  async remove(id) {
    await this.change((data) => {
      if (!data.items.some((row) => row.asset.id === id)) return
      data.items = data.items.filter((row) => row.asset.id !== id)
      data.removed.push(id)
    }, true)
  }

  async maintain() {
    if (!this.data.removed.length) return
    const state = await this.assets.state()
    const held = new Set(Object.values(state.owners).flat())
    const resources = this.data.removed.filter((id) => !held.has(id)).map((id) => ({ id }))
    if (!resources.length) return
    const { deleted, missing, skipped } = await this.assets.collectUnused({ resources })
    const reclaimed = new Set([
      ...[...deleted, ...missing].map((row) => row.id),
      ...skipped.filter((row) => row.reason === 'not_registered').map((row) => row.asset.id)
    ])
    if (reclaimed.size)
      await this.change((data) => {
        data.removed = data.removed.filter((id) => !reclaimed.has(id))
      })
  }

  async start(key) {
    const source = await this.source.item(key)
    const job = await this.change((data) => {
      requireValue(
        !this.closed && !data.items.some((row) => row.key === key),
        'background_installed',
        '这张背景已下载。'
      )
      const existing = data.jobs.find(
        (row) => row.source.key === key && !['cancelled', 'succeeded'].includes(row.status)
      )
      if (existing) return existing
      const next = { id: uuid(), kind: 'background', source, status: 'running' }
      data.jobs.push(next)
      return next
    })
    if (job.status === 'running') this.launch(job)
    return job
  }

  launch(job) {
    if (this.tasks.has(job.id) || this.closed) return
    const task = { controller: new AbortController() }
    this.tasks.set(job.id, task)
    task.done = this.run(job, task).finally(() => this.tasks.delete(job.id))
    task.done.catch((error) => this.assets.reportMaintenance(error))
  }

  async run(job, task) {
    const signal = task.controller.signal
    let owner
    try {
      const bytes = await this.source.download(job.source, signal, (progress) => {
        this.downloadJobs.progress(job.id, () => ({ progress }))
      })
      await this.change((data) => {
        signal.throwIfAborted()
        const current = data.jobs.find((row) => row.id === job.id)
        current.status = 'installing'
        return current
      })
      owner = await this.assets.createTemporaryOwner('import')
      const info = await this.assets.importAsset(
        { kind: 'bytes', bytes, name: job.source.filename, mediaType: 'image/png' },
        owner,
        'chat-image',
        signal
      )
      await this.change((data) => {
        signal.throwIfAborted()
        data.items.push({
          asset: info.asset,
          key: job.source.key,
          name: job.source.name,
          filename: job.source.filename
        })
        return Object.assign(
          data.jobs.find((row) => row.id === job.id),
          { status: 'succeeded', completedAt: Date.now() }
        )
      }, true)
    } catch (error) {
      await this.change((data) =>
        Object.assign(
          data.jobs.find((row) => row.id === job.id),
          {
            status: task.stop ?? 'failed',
            problem: signal.aborted ? undefined : problem(error)
          }
        )
      )
    } finally {
      if (owner) await this.assets.releaseWhenPossible(owner)
    }
  }

  async control(id, action) {
    return this.queue.run('control:' + id, async () => {
      const job = this.data.jobs.find((row) => row.id === id)
      requireValue(job && ['pause', 'cancel', 'resume'].includes(action))
      if (['succeeded', 'cancelled'].includes(job.status)) return
      requireValue(job.status !== 'installing', 'background_installing', '图片正在保存。')
      if (action === 'resume') {
        requireValue(!this.closed && ['paused', 'interrupted', 'failed'].includes(job.status))
        const next = await this.change((data) =>
          Object.assign(
            data.jobs.find((row) => row.id === id),
            { status: 'running', problem: undefined, progress: undefined }
          )
        )
        this.launch(next)
        return
      }
      const status = action === 'pause' ? 'paused' : 'cancelled'
      const task = this.tasks.get(id)
      if (task) {
        task.stop = status
        task.controller.abort()
        await task.done
      } else
        await this.change((data) =>
          Object.assign(
            data.jobs.find((row) => row.id === id),
            { status }
          )
        )
    })
  }

  async close() {
    this.closed = true
    for (const task of this.tasks.values()) {
      task.stop = 'interrupted'
      task.controller.abort()
    }
    await Promise.allSettled([...this.tasks.values()].map((task) => task.done))
    this.events.clear()
    this.downloadJobs.close()
  }
}
