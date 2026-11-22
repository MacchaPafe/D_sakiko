import path from 'node:path'
import { rm } from 'node:fs/promises'
import { bangDreamCharacters } from '../characters/bang-dream.js'
import {
  copy,
  readJson,
  requireValue,
  safeId,
  SerialQueue,
  uuid,
  writeJson,
  problem
} from '../core/support.js'
import { DownloadJobFeed } from '../core/download-job-feed.js'
import { Live2dImporter } from './interface.js'
import { OutfitSources } from './sources.js'
import { extractAssetReferences } from '../resources/references.js'

/** @typedef {{characterId: string, formId: string}} OutfitTarget 安装和管理始终指向同一个已有形态。 */
/** @typedef {{id: string, target: OutfitTarget, item: import('./sources.js').OnlineOutfit, status: 'running'|'installing'|'interrupted'|'failed'|'cancelled'|'succeeded', progress: {completed: number, total: number}|null, problem?: object, modelId?: string, completedAt?: number}} OutfitJob 可恢复的服装下载任务。 */

function identity(item) {
  return { kind: item.source, id: item.characterId + ':' + item.id }
}

function findSource(form, source) {
  return form.models.find(
    (model) => model.source?.kind === source.kind && model.source.id === source.id
  )
}

function active(job) {
  return job.status === 'running' || job.status === 'installing'
}

/** 管理既有角色形态的服装，从本地文件夹或受支持的在线目录安装。 */
export class Outfits {
  constructor({ appDataRoot, cacheRoot, assets, characters, conversations, fetch }) {
    Object.assign(this, { assets, characters, conversations })
    this.file = path.join(appDataRoot, 'outfit-jobs.json')
    this.cache = path.join(cacheRoot, 'outfit-downloads')
    this.importer = new Live2dImporter({ assets, cacheRoot })
    this.sources = new OutfitSources(fetch)
    /** @type {Map<string, OutfitJob>} */
    this.jobs = new Map()
    this.downloadJobs = new DownloadJobFeed()
    this.tasks = new Map()
    this.removedAssets = new Set()
    this.localImports = new Set()
    this.queue = new SerialQueue()
    this.saving = new SerialQueue()
    this.closed = false
  }

  async initialize() {
    const saved = await readJson(this.file, { jobs: [], removedAssets: [] })
    let changed = false
    for (const job of Array.isArray(saved) ? saved : saved.jobs) {
      safeId(job.id)
      if (active(job)) {
        job.status = 'interrupted'
        changed = true
      }
      this.jobs.set(job.id, job)
      this.downloadJobs.publish(job)
    }
    for (const id of saved.removedAssets ?? []) this.removedAssets.add(safeId(id))
    if (changed) await this.saveJobs()
  }

  async saveJobs() {
    return this.saving.run('jobs', () =>
      writeJson(this.file, {
        jobs: [...this.jobs.values()],
        removedAssets: [...this.removedAssets]
      })
    )
  }

  async saveJob(job) {
    await this.saveJobs()
    this.downloadJobs.publish(job)
  }

  /** 回收服装删除后释放的资源，现有场景及其它使用方仍按媒体库引用管理。 */
  async maintain() {
    if (!this.removedAssets.size) return
    return this.queue.run('maintenance:reclaim', async () => {
      const state = await this.assets.state()
      const candidates = [...this.removedAssets].filter(
        (id) => !Object.values(state.owners).some((ids) => ids.includes(id))
      )
      if (!candidates.length) return
      const refs = candidates.filter((id) => Object.hasOwn(state.assets, id)).map((id) => ({ id }))
      if (refs.length) await this.assets.collectUnused({ resources: refs })
      const current = await this.assets.state()
      let changed = false
      for (const id of candidates) {
        if (!Object.hasOwn(current.assets, id)) changed = this.removedAssets.delete(id) || changed
      }
      if (changed) await this.saveJobs()
    })
  }

  async close() {
    this.closed = true
    for (const task of this.tasks.values()) task.controller.abort()
    await Promise.allSettled([...this.tasks.values()].map((task) => task.done))
    this.downloadJobs.close()
  }

  async target(target) {
    const characterId = safeId(target?.characterId)
    const formId = safeId(target?.formId)
    const role = await this.characters.get(characterId)
    requireValue(role, 'character_missing', '角色已不存在。')
    const form = role.forms.find((item) => item.id === formId)
    requireValue(form, 'form_missing', '角色人格已不存在。')
    return { role, form }
  }

  /** @returns {Array<{id: string, name: string, displayName: string, themeColor: string, band: string | null, aliases: string[]}>} 可确认的邦角色目录及其预设色。 */
  listCharacters() {
    return [...bangDreamCharacters.values()].map(
      ({ id, name, displayName, themeColor, band, aliases }) => ({
        id,
        name,
        displayName,
        themeColor,
        band,
        aliases: [...aliases]
      })
    )
  }

  /**
   * @param {string} id 本地角色 ID。
   * @param {string|null} affiliation 目录 ID 或用户明确标记为其他角色。
   * @returns {Promise<void>} 完成归属更新。
   */
  async setCharacter(id, affiliation) {
    safeId(id)
    requireValue(
      affiliation === null || bangDreamCharacters.has(affiliation),
      'invalid_bang_dream_character'
    )
    return this.queue.run(id, async () => {
      const role = await this.characters.get(id)
      requireValue(role, 'character_missing', '角色已不存在。')
      if (role.bangDreamId === affiliation) return
      role.bangDreamId = affiliation
      await this.characters.save(role)
    })
  }

  /**
   * @param {OutfitTarget} target 当前选中的角色形态。
   * @param {boolean} [refresh] 请求新目录。
   * @returns {Promise<{items: object[], problems: object[], affiliation: string|null}>} 可下载服装及安装标志。
   */
  async catalog(target, refresh = false) {
    const { role, form } = await this.target(target)
    const character = bangDreamCharacters.get(role.bangDreamId)
    if (!character) return { items: [], problems: [], affiliation: null }
    const catalog = await this.sources.catalog(character, refresh === true)
    return {
      affiliation: character.id,
      items: catalog.items.map((item) => ({
        id: item.id,
        name: item.name,
        source: item.source,
        version: item.version,
        thumbnail: Boolean(item.thumbnail),
        sizeBytes: item.sizeBytes ?? null,
        installed: Boolean(findSource(form, identity(item)))
      })),
      problems: catalog.problems
    }
  }

  async onlineItem(target, source, id) {
    const { role, form } = await this.target(target)
    const character = bangDreamCharacters.get(role.bangDreamId)
    requireValue(character, 'bang_dream_character_required', '请先确认该角色的 BanG Dream! 归属。')
    requireValue(['bestdori', 'modelscope'].includes(source) && typeof id === 'string')
    const catalog =
      source === 'bestdori' ? await this.sources.v2(character) : await this.sources.v3(character)
    const item = catalog.find((entry) => entry.id === id)
    requireValue(item, 'outfit_not_available', '这套服装已不在来源目录中，请刷新。')
    return { role, form, item }
  }

  /**
   * @param {OutfitTarget} target 在线目录的目标形态。
   * @param {import('./sources.js').OutfitSource} source 服装来源。
   * @param {string} id 服装 ID。
   * @returns {Promise<string|null>} 已核验的图片 Data URL。
   */
  async getThumbnail(target, source, id) {
    const { item } = await this.onlineItem(target, source, id)
    const thumbnail = await this.sources.thumbnail(item, AbortSignal.timeout(12000))
    return thumbnail
      ? 'data:image/' + thumbnail.type + ';base64,' + thumbnail.bytes.toString('base64')
      : null
  }

  async addModel(target, model, affiliation = undefined) {
    return this.queue.run(target.characterId, async () => {
      const { role, form } = await this.target(target)
      requireValue(
        affiliation === undefined || role.bangDreamId === affiliation,
        'outfit_character_changed',
        '角色的 BanG Dream! 归属已改变，请重新选择服装。'
      )
      const prior = findSource(form, model.source)
      if (!prior) {
        form.models.push(model)
        form.defaultModelId ??= model.id
        await this.characters.save(role)
      }
      const id = prior?.id ?? model.id
      // 首次安装（或上次安装交接中断）时补齐此前没有模型的选中形态。
      if (form.defaultModelId === id) {
        await this.conversations.reassignOutfit({
          ...target,
          previousId: null,
          modelId: id
        })
      }
      return { modelId: id, alreadyInstalled: Boolean(prior) }
    })
  }

  /**
   * @param {OutfitTarget} target 当前选中的角色形态。
   * @param {string} directory 原生文件选择器明确授权的目录，不能由 HTTP 客户端提供。
   * @returns {Promise<{modelId: string, alreadyInstalled: boolean}>} 已入库模型身份。
   */
  async importDirectory(target, directory) {
    const operation = { characterId: target.characterId }
    this.localImports.add(operation)
    try {
      await this.target(target)
      let duplicate = []
      const result = await this.assets.using({}, async (owner) => {
        const model = await this.importer.importDirectory(directory, owner, {})
        const installed = await this.addModel(target, model)
        if (installed.alreadyInstalled) duplicate = extractAssetReferences(model.presentation)
        return installed
      })
      if (duplicate.length) await this.reclaim(duplicate)
      return result
    } finally {
      this.localImports.delete(operation)
    }
  }

  /** @param {string} characterId 角色身份。 @returns {boolean} 正在为该角色导入或下载安装服装。 */
  isInstalling(characterId) {
    return (
      [...this.localImports].some((operation) => operation.characterId === characterId) ||
      [...this.jobs.values()].some((job) => job.target.characterId === characterId && active(job))
    )
  }

  async reclaim(refs) {
    for (const { id } of refs) this.removedAssets.add(id)
    await this.saveJobs().catch((error) => this.assets.reportMaintenance(error))
    await this.maintain().catch((error) => this.assets.reportMaintenance(error))
  }

  /**
   * @param {OutfitTarget} target 所属角色形态。
   * @param {string} modelId 现有服装 ID。
   * @param {string} name 展示名。
   * @returns {Promise<void>} 完成重命名。
   */
  async rename(target, modelId, name) {
    requireValue(
      typeof name === 'string' && name.trim().length > 0 && name.trim().length <= 120,
      'invalid_outfit_name',
      '请输入 1 到 120 个字符的服装名称。'
    )
    return this.editModel(target, modelId, (model) => {
      model.name = name.trim()
    })
  }

  /**
   * @param {OutfitTarget} target 所属角色形态。
   * @param {string} modelId 现有服装 ID。
   * @returns {Promise<void>} 完成默认服装修改。
   */
  async setDefault(target, modelId) {
    return this.editModel(target, modelId, (model, form) => {
      form.defaultModelId = model.id
    })
  }

  async editModel(target, modelId, edit) {
    return this.queue.run(safeId(target.characterId), async () => {
      const { role, form } = await this.target(target)
      const model = form.models.find((item) => item.id === modelId)
      requireValue(model, 'model_missing', '服装已不存在。')
      edit(model, form)
      await this.characters.save(role)
    })
  }

  async removal(target, modelId, dryRun, expectedDefaultId) {
    return this.queue.run(safeId(target.characterId), async () => {
      const { role, form } = await this.target(target)
      const replacement = form.models.find((model) => model.id === form.defaultModelId)
      const removed = form.models.find((model) => model.id === modelId)
      requireValue(removed, 'model_missing', '服装已不存在。')
      requireValue(
        replacement && replacement.id !== modelId,
        'default_outfit_in_use',
        '请先把另一套服装设为默认。'
      )
      requireValue(
        dryRun || replacement.id === expectedDefaultId,
        'outfit_default_changed',
        '默认服装已改变，请再次确认删除。'
      )
      const reassign = () =>
        this.conversations.reassignOutfit(
          { ...target, previousId: modelId, modelId: replacement.id, dryRun },
          async () => {
            form.models = form.models.filter((item) => item.id !== modelId)
            await this.characters.save(role)
          }
        )
      const result = dryRun
        ? await reassign()
        : await this.characters.withRemovingOutfit(target, modelId, reassign)
      if (!dryRun) {
        await this.reclaim(extractAssetReferences(removed.presentation))
      }
      return { ...result, defaultId: replacement.id, defaultName: replacement.name }
    })
  }

  /** @param {OutfitTarget} target 所属角色形态。 @param {string} modelId 待删服装。 @returns {Promise<{count: number, busy: boolean, defaultId: string, defaultName: string}>} 删除影响。 */
  previewRemoval(target, modelId) {
    return this.removal(target, modelId, true)
  }

  /** @param {OutfitTarget} target 所属角色形态。 @param {string} modelId 待删服装。 @param {string} defaultId 用户确认时的默认服装。 @returns {Promise<object>} 删除结果。 */
  remove(target, modelId, defaultId) {
    return this.removal(target, modelId, false, defaultId)
  }

  /**
   * @param {OutfitTarget} [target] 省略时供下载中心查看所有角色形态。
   * @returns {OutfitJob[]} 最新登记的任务在前。
   */
  listJobs(target) {
    return copy(
      [...this.jobs.values()]
        .filter(
          ({ target: job }) =>
            target === undefined ||
            (target?.characterId === job.characterId && target?.formId === job.formId)
        )
        .reverse()
    )
  }

  /**
   * @param {OutfitTarget} target 安装到已有形态。
   * @param {import('./sources.js').OutfitSource} source 来源。
   * @param {string} id 在线服装 ID。
   * @returns {Promise<OutfitJob>} 已受理的持久任务。
   */
  async start(target, source, id) {
    return this.queue.run(safeId(target.characterId), async () => {
      const { role, form, item } = await this.onlineItem(target, source, id)
      const existing = findSource(form, identity(item))
      requireValue(!existing, 'outfit_already_installed', '这套服装已安装。')
      const previous = this.listJobs(target).find(
        (job) =>
          job.item.source === source &&
          job.item.id === id &&
          job.item.characterId === item.characterId &&
          !['cancelled', 'succeeded'].includes(job.status)
      )
      if (previous) return previous
      const job = {
        id: uuid(),
        target: { characterId: role.id, formId: form.id },
        item,
        status: 'interrupted',
        progress: null
      }
      this.jobs.set(job.id, job)
      await this.launch(job)
      return copy(job)
    })
  }

  async launch(job) {
    requireValue(!this.closed, 'backend_closed')
    job.status = 'running'
    delete job.problem
    await this.saveJob(job)
    if (this.closed) {
      job.status = 'interrupted'
      await this.saveJob(job)
      return
    }
    const controller = new AbortController()
    const task = { controller, cancel: false, done: null }
    this.tasks.set(job.id, task)
    task.done = this.run(job, task).finally(() => this.tasks.delete(job.id))
    void task.done.catch((error) => this.assets.reportMaintenance(error))
  }

  async run(job, task) {
    const directory = path.join(this.cache, job.id)
    const { signal } = task.controller
    const readProgress = () => ({ progress: job.progress })
    const progress = (completed, total) => {
      job.progress = { completed, total }
      this.downloadJobs.progress(job.id, readProgress)
    }
    const { target, item } = job
    try {
      const { role, form } = await this.target(target)
      requireValue(
        role.bangDreamId === item.characterId,
        'outfit_character_changed',
        '角色的 BanG Dream! 归属已改变，请重新选择服装。'
      )
      const existing = findSource(form, identity(item))
      if (existing) {
        await this.addModel(target, existing, item.characterId)
        job.modelId = existing.id
      } else {
        const payload =
          item.source === 'bestdori'
            ? await this.sources.downloadV2(item, path.join(directory, 'model'), signal, progress)
            : await this.sources.downloadArchive(item, directory, signal, progress)
        signal.throwIfAborted()
        job.status = 'installing'
        await this.saveJob(job)
        const options = { name: item.name, source: identity(item) }
        await this.assets.using({}, async (owner) => {
          const model =
            item.source === 'bestdori'
              ? await this.importer.importDirectory(
                  payload,
                  owner,
                  { ...options, version: 'v2' },
                  signal
                )
              : await this.importer.importPackage(payload, owner, options, signal)
          signal.throwIfAborted()
          const result = await this.addModel(target, model, item.characterId)
          job.modelId = result.modelId
        })
      }
      job.status = 'succeeded'
      job.completedAt = Date.now()
    } catch (error) {
      job.status = signal.aborted ? (task.cancel ? 'cancelled' : 'interrupted') : 'failed'
      if (!signal.aborted) job.problem = problem(error)
    } finally {
      await this.saveJob(job)
      if (['cancelled', 'succeeded'].includes(job.status))
        await rm(directory, { recursive: true, force: true })
    }
  }

  /**
   * @param {string} id 持久任务 ID。
   * @param {'pause'|'resume'|'cancel'} action 把任务留待手动继续或取消并清理下载分片。
   * @returns {Promise<OutfitJob>} 当前任务快照。
   */
  async control(id, action) {
    safeId(id)
    requireValue(['pause', 'resume', 'cancel'].includes(action))
    return this.queue.run('job:' + id, async () => {
      const job = this.jobs.get(id)
      requireValue(job, 'outfit_job_missing', '找不到下载任务。')
      const task = this.tasks.get(id)
      if (action === 'resume' && !task && ['failed', 'interrupted'].includes(job.status)) {
        await this.launch(job)
      } else if (task && job.status === 'running' && action !== 'resume') {
        task.cancel = action === 'cancel'
        task.controller.abort()
        await task.done
      } else if (!task && action === 'cancel' && ['failed', 'interrupted'].includes(job.status)) {
        job.status = 'cancelled'
        delete job.problem
        await this.saveJob(job)
        await rm(path.join(this.cache, id), { recursive: true, force: true })
      }
      return copy(job)
    })
  }
}
