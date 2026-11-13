import path from 'node:path'
import { mkdir, realpath, link, rm, readdir, stat } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import {
  readJson,
  writeJson,
  uuid,
  safeId,
  requireValue,
  fingerprint,
  problem,
  SnapshotBus,
  SerialQueue,
  copy
} from '../core/support.js'
import { extractAssetReferences } from '../resources/references.js'
import { processUsesResources } from '../resources/process.js'
import { SetupCatalog, setupGroups } from './catalog.js'
import { scanLegacyDirectory, validateLegacySource } from './legacy.js'
import { checkSpace, copyNew, sourcePath, stamp, walk } from './files.js'
import { downloadFile } from './download.js'
import { extractArchive, prepareRuntime } from './installers.js'
import { bindGsv, characterDefinition, importConnections, legacyConnections } from './bindings.js'
import { selectGptSovitsFiles } from '../../shared/contracts/speech-resources.js'
import { pythonChatId } from '../storage/python-import-interface.js'

const legacyGroups = ['characters', 'chats', 'connections', 'gsv']

function downloadFiles(components) {
  return [
    ...new Map(
      components.flatMap((component) => component.files.map((file) => [file.sha256, file]))
    ).values()
  ]
}

function legacySummary(scan, builtinConnection) {
  const { definitions, pending, useBuiltin } = legacyConnections(scan.config)
  const connectionCount = new Set(definitions.map(({ definition }) => definition.id)).size
  return {
    characters: scan.roles.length + (scan.personas?.length ?? 0),
    chats: scan.chats.length,
    connections: connectionCount + Number(useBuiltin && !!builtinConnection),
    pendingConnections: pending.length + Number(useBuiltin && !builtinConnection),
    gsv: Object.values(scan.gsv).filter(Boolean).length
  }
}

function legacyPersonaId(persona) {
  return 'legacy-persona-' + fingerprint(persona.persona_id ?? persona).slice(0, 24)
}

/**
 * @typedef {object} SetupJob
 * @property {string} id 持久身份，计划与任务共用，重试不生成副本。
 * @property {'planned'|'running'|'paused'|'failed'|'cancelled'|'succeeded'} status 任务状态。
 * @property {string} phase 当前阶段。
 * @property {Object<string, import('../../shared/contracts/common.js').AssetRef>} refs 已持久准备的文件。
 */

/** 初始化和附加资源的准备边界；来源路径、密钥、安装记录均不进入公开快照。 */
export class ResourceSetup {
  constructor({
    appDataRoot,
    resourceLibraryRoot,
    cacheRoot,
    assets,
    settings,
    characters,
    store,
    catalogDirectory = fileURLToPath(new URL('../../../resources/setup/', import.meta.url)),
    catalog,
    fetch: transport = fetch,
    builtinConnection,
    importChats,
    ...options
  }) {
    Object.assign(this, {
      assets,
      settings,
      characters,
      store,
      transport,
      builtinConnection,
      importChats
    })
    this.directory = path.join(appDataRoot, 'setup')
    this.library = resourceLibraryRoot
    this.cache = path.join(cacheRoot, 'setup')
    this.catalog =
      catalog ?? new SetupCatalog({ directory: catalogDirectory, fetch: transport, ...options })
    this.bus = new SnapshotBus()
    this.queue = new SerialQueue()
    this.sources = new Map()
    this.running = new Map()
    this.closed = false
    assets.registerOwnerReader('setup', async (id) => (await this.readJob(id))?.refs ?? {})
    assets.registerOwnerReader(
      'installation',
      async (id) => (await this.installations())[id] ?? null
    )
  }

  jobFile(id) {
    return path.join(this.directory, 'jobs', safeId(id) + '.json')
  }
  readJob(id) {
    return readJson(this.jobFile(id))
  }
  installations() {
    return readJson(path.join(this.directory, 'installations.json'), {})
  }

  /** 在资源恢复前登记 owner，在资源恢复后将中断任务置为可继续。 */
  async initialize() {
    await mkdir(path.join(this.directory, 'jobs'), { recursive: true })
    for (const name of await readdir(path.join(this.directory, 'jobs'))) {
      if (!/^[a-zA-Z0-9-]+\.json$/.test(name)) continue
      const job = await readJson(path.join(this.directory, 'jobs', name))
      if (job.status === 'running') {
        job.status = 'paused'
        job.phase = 'interrupted'
        const item = job.items?.[job.currentItem]
        if (item?.status === 'committing') item.status = 'paused'
        await this.save(job)
      }
      if (job.phase === 'planning' || (job.status === 'planned' && job.expiresAt < Date.now()))
        await this.cancel(job)
    }
    await this.maintainRuntimes()
  }

  /** 仅可信宿主可调用，HTTP 方法表不暴露该方法。 */
  async grantLegacySource(directory) {
    requireValue(path.isAbsolute(directory))
    const root = await realpath(directory)
    requireValue((await stat(root)).isDirectory(), 'legacy_directory_invalid')
    const overlaps = (a, b) => {
      const relative = path.relative(a, b)
      return (
        relative === '' ||
        (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative))
      )
    }
    for (const directory of [this.library, this.directory, this.cache]) {
      await mkdir(directory, { recursive: true })
      const other = await realpath(directory)
      requireValue(
        !overlaps(root, other) && !overlaps(other, root),
        'legacy_directory_overlaps_data',
        '旧版来源不能包含新版数据目录。'
      )
    }
    const sourceId = uuid()
    this.sources.set(sourceId, root)
    return { sourceId, name: path.basename(root) }
  }

  /** 只读扫描；完整转换材料写入私有记录，返回可确认摘要。
   * @param {string} sourceId 宿主授权的来源身份。
   * @returns {Promise<object>} 不含密钥和绝对路径的扫描摘要。
   */
  async scanLegacy(sourceId) {
    const root = this.sources.get(sourceId)
    requireValue(root, 'legacy_source_not_authorized', '请通过目录选择器重新选择旧版本文件夹。')
    const scan = await scanLegacyDirectory(root)
    const summary = legacySummary(scan, this.builtinConnection)
    const scanId = uuid()
    await writeJson(path.join(this.directory, 'scans', scanId + '.json'), scan)
    return {
      scanId,
      characters: scan.roles.map((role) => ({
        id: role.id,
        name: role.name,
        models: role.forms.reduce((sum, form) => sum + form.models.length, 0),
        voice: role.forms.some((form) => !!form.voice)
      })),
      chatCount: scan.chats.length,
      hasConnections: summary.connections > 0 || summary.pendingConnections > 0,
      connectionCount: summary.connections,
      pendingConnectionCount: summary.pendingConnections,
      gsvFileCount: summary.gsv,
      personaCount: scan.personas.length,
      availableCategories: Object.fromEntries(legacyGroups.map((key) => [key, summary[key] > 0])),
      canImport: legacyGroups.some((key) => summary[key] > 0),
      warnings: scan.warnings,
      logicalBytes: Object.values(scan.files).reduce((sum, file) => sum + file.sizeBytes, 0)
    }
  }

  async componentReady(component, installed) {
    if (component.kind === 'voice') {
      const role = await this.voiceTarget(component.characterId)
      if (role?.forms.length && role.forms.every((form) => form.voice)) {
        const usable = await Promise.all(role.forms.map((form) => this.voiceReady(form.voice)))
        if (usable.every(Boolean)) return true
      }
      return false
    }
    const item = installed[component.id]
    if (!item || item.status !== 'ready' || (component.target && item.target !== component.target))
      return false
    try {
      for (const ref of extractAssetReferences(item.refs ?? {})) await this.assets.localPath(ref)
      for (const file of Object.values(item.runtime ?? {})) await stat(file)
      return true
    } catch {
      return false
    }
  }

  /** 返回三个产品分组及整体可选性，不暴露内部组件开关。
   * @param {{refresh?: boolean, accelerator?: 'cpu'|'cuda', selection?: import('../../shared/contracts/setup.js').ResourceChoice[]}} options 清单刷新、加速方式和用于估算的选择。
   * @returns {Promise<object>} 三组资源的可用性、已准备状态和空间提示。
   */
  async getCatalog(options = {}) {
    const components = await this.catalog.components(options)
    if (options.selection !== undefined)
      await this.catalog.select(options.selection, { ...options, refresh: false })
    const installed = await this.installations()
    const groups = copy(setupGroups)
    const selectedForEstimate = []
    let selectionAvailable = true
    for (const group of groups)
      for (const option of group.options) {
        const selected = await this.catalog.select([option.id], { ...options, refresh: false })
        const voice = selected.find((component) => component.kind === 'voice')
        const target = voice ? await this.voiceTarget(voice.characterId) : null
        const missing = []
        for (const component of selected)
          if (!(await this.componentReady(component, installed))) {
            if (component.id === 'gsv.models')
              await this.reuseGsv(component, installed['gsv.models'])
            missing.push(component)
          }
        option.ready = missing.length === 0
        option.available = (!voice || !!target) && missing.every((item) => item.available)
        option.sizeKnown = option.available
        option.unavailableReason =
          missing.find((item) => !item.available)?.unavailableReason ?? null
        option.downloadBytes = downloadFiles(missing).reduce((sum, file) => sum + file.sizeBytes, 0)
        if (voice) {
          option.characterId = target?.id ?? null
          if (!target) option.unavailableReason = '角色不存在，无法安装这份语音。'
          option.requiresSelection = missing.some((item) => item.id.startsWith('gsv.'))
            ? 'gsv'
            : null
        }
        if (options.selection?.includes(option.id)) {
          selectedForEstimate.push(...missing)
          selectionAvailable &&= option.available
        }
      }
    return {
      groups,
      selectionDownloadBytes:
        options.selection && selectionAvailable
          ? downloadFiles(selectedForEstimate).reduce((sum, file) => sum + file.sizeBytes, 0)
          : null,
      platform: this.catalog.platform,
      arch: this.catalog.arch,
      accelerator: options.accelerator ?? 'cpu',
      runtimePublished: components
        .filter((item) => item.kind === 'runtime')
        .every((item) => item.available)
    }
  }

  /** 生成计划时真正保留候选链接，因此确认页空间估计不依赖同盘猜测。
   * @param {import('../../shared/contracts/setup.js').SetupPlanRequest} options 旧版导入范围或产品资源选择。
   * @returns {Promise<import('../../shared/contracts/setup.js').SetupSnapshot>} 可以确认和执行的计划快照。
   */
  async createPlan(options) {
    return this.queue.run('admission', () => this.createPlanUnlocked(options))
  }

  async createPlanUnlocked(options) {
    requireValue(['legacy', 'download'].includes(options?.kind), 'invalid_setup_plan')
    requireValue(!this.closed, 'setup_closed')
    const job = {
      id: uuid(),
      kind: options.kind,
      status: 'planned',
      phase: 'planning',
      createdAt: Date.now(),
      expiresAt: Date.now() + 86400000,
      refs: {},
      completed: [],
      results: [],
      warnings: [],
      progress: null,
      files: {},
      selected: [],
      planBytes: { logical: 0, added: 0, download: 0, cache: 0 },
      options: {}
    }
    await this.save(job)
    try {
      if (options.kind === 'legacy') {
        const scan = await readJson(
          path.join(this.directory, 'scans', safeId(options.scanId) + '.json')
        )
        requireValue(scan, 'legacy_scan_missing')
        await validateLegacySource(scan)
        job.scan = scan
        const reused = await readJson(
          path.join(this.directory, 'sources', scan.identity + '.json'),
          {}
        )
        job.warnings = scan.warnings
        const selection = options.include ?? {
          characters: true,
          connections: true,
          chats: true,
          gsv: true
        }
        requireValue(
          Object.keys(selection).every((key) =>
            ['characters', 'connections', 'chats', 'gsv'].includes(key)
          ) && Object.values(selection).every((value) => typeof value === 'boolean')
        )
        const summary = legacySummary(scan, this.builtinConnection)
        requireValue(
          legacyGroups.some((key) => selection[key] && summary[key] > 0),
          'legacy_nothing_to_import',
          '所选类别中没有可导入的资料。'
        )
        job.options.include = selection
        job.items = Object.fromEntries(
          legacyGroups.map((key) => [
            key,
            {
              total: summary[key],
              completed: 0,
              status: selection[key] ? 'waiting' : 'skipped'
            }
          ])
        )
        const keys = new Set()
        if (selection.characters) {
          for (const persona of scan.personas ?? []) if (persona.avatar) keys.add(persona.avatar)
          const existingIds = new Set((await this.characters.list()).map((role) => role.id))
          for (const role of scan.roles) {
            if (existingIds.has(role.id)) continue
            for (const form of role.forms) {
              for (const model of form.models) keys.add(model.key)
            }
            for (const key of Object.values(role.voiceFiles)) if (key) keys.add(key)
            if (role.avatar) keys.add(role.avatar)
          }
          // 外部引用参考音频也属于角色资料，不要求与角色目录相邻。
          for (const role of scan.roles.filter((role) => !existingIds.has(role.id)))
            for (const form of role.forms)
              if (form.voice) {
                keys.add(form.voice.defaultReference.audio)
                for (const reference of Object.values(form.voice.emotions))
                  keys.add(reference.audio)
              }
        }
        if (selection.gsv) for (const key of Object.values(scan.gsv)) if (key) keys.add(key)
        if (selection.chats) for (const key of Object.values(scan.media)) if (key) keys.add(key)
        for (const key of keys) {
          const file = copy(scan.files[key])
          file.mode = 'copy'
          const previous = reused[key]
          if (previous?.fingerprint === fingerprint(file.stamp ?? file.stamps)) {
            try {
              await this.assets.localPath(previous.ref)
              await this.assets.addReferences('setup:' + job.id, [previous.ref])
              job.refs[key] = previous.ref
              file.mode = 'reuse'
            } catch {
              /* 已删除的资源由本次计划重新准备。 */
            }
          }
          if (file.hardlink && file.mode !== 'reuse') {
            const destination = this.modelDestination(job.id, key, file.name)
            await mkdir(path.dirname(destination), { recursive: true })
            try {
              await link(await sourcePath(scan.root, file.name), destination)
              file.mode = 'hardlink'
            } catch (error) {
              if (
                !['EXDEV', 'EPERM', 'EACCES', 'ENOTSUP', 'EMLINK', 'ENOSYS', 'EINVAL'].includes(
                  error.code
                )
              )
                throw error
            }
          }
          job.files[key] = file
          job.planBytes.logical += file.sizeBytes
          if (file.mode === 'copy') job.planBytes.added += file.sizeBytes
        }
      } else {
        const installed = await this.installations()
        job.options.selection = copy(options.selection)
        job.voicePreferenceRevision = (await this.settings.voicePreference()).revision
        for (const component of await this.catalog.select(options.selection, {
          accelerator: options.accelerator ?? 'cpu'
        })) {
          if (component.kind === 'voice') {
            const target = await this.voiceTarget(component.characterId)
            requireValue(target, 'character_missing', '请先完成角色初始化，再下载角色语音。')
            component.targetCharacterId = target.id
          }
          if (await this.componentReady(component, installed)) continue
          requireValue(
            component.available,
            'runtime_not_published',
            component.unavailableReason ?? '所需资源尚未发布。'
          )
          if (component.id === 'gsv.models')
            await this.reuseGsv(component, installed['gsv.models'], job)
          job.selected.push(component)
          for (const file of component.files) {
            job.planBytes.added += file.expandedBytes ?? file.sizeBytes
            job.planBytes.logical += file.expandedBytes ?? file.sizeBytes
          }
          job.planBytes.added += component.installBytes ?? 0
        }
        job.planBytes.download = downloadFiles(job.selected).reduce(
          (sum, file) => sum + file.sizeBytes,
          0
        )
        job.planBytes.cache = job.planBytes.download
      }
      job.volumes = await this.space(job)
      job.phase = 'planned'
      await this.save(job)
      return this.snapshot(job)
    } catch (error) {
      await this.assets.releaseWhenPossible('setup:' + job.id)
      await rm(path.join(this.library, 'models', 'setup', job.id), { recursive: true, force: true })
      await rm(this.jobFile(job.id), { force: true })
      throw error
    }
  }

  modelDestination(id, key, name) {
    return path.join(
      this.library,
      'models',
      'setup',
      safeId(id),
      name ? 'legacy/' + name : safeId(key)
    )
  }
  async space(job) {
    // 应用数据与资源库可能跨卷；应用资料按实际用途归属计入，解包和环境均在资源卷。
    const remaining = Object.entries(job.files)
      .filter(([key, file]) => file.mode === 'copy' && !job.refs[key])
      .map(([, file]) => file)
    const application = remaining
      .filter((file) => ['avatar', 'attachment', 'chat-image'].includes(file.purpose))
      .reduce((sum, file) => sum + file.sizeBytes, 0)
    let library = remaining.reduce((sum, file) => sum + file.sizeBytes, 0) - application
    let cache = 0
    const missingDownloads = new Set()
    for (const component of job.selected.filter(
      (item) => !job.completed.includes(item.id) && !job.prepared?.[item.id]
    )) {
      library += component.installBytes ?? 0
      for (const file of component.files) {
        library += file.expandedBytes ?? file.sizeBytes
        if (component.kind === 'voice' && /\.(wav|mp3)$/i.test(file.path)) library += file.sizeBytes
        if (missingDownloads.has(file.sha256)) continue
        const destination = path.join(this.cache, file.sha256)
        const existing = await stat(destination).catch(() => null)
        if (existing?.size === file.sizeBytes) continue
        missingDownloads.add(file.sha256)
        const partial = await stat(destination + '.part').catch(() => null)
        const reusable = partial && partial.size <= file.sizeBytes ? partial.size : 0
        cache += file.sizeBytes - reusable
      }
    }
    return checkSpace([
      { directory: this.library, bytes: Math.max(0, library) },
      { directory: this.directory, bytes: application },
      { directory: this.cache, bytes: cache }
    ])
  }
  snapshot(job) {
    const downloaded = Object.values(job.downloaded ?? {}).reduce((sum, bytes) => sum + bytes, 0)
    const rate =
      job.status === 'running' && job.phase === 'downloading' ? (job.downloadRate ?? null) : null
    return {
      id: job.id,
      kind: job.kind,
      status: job.status,
      phase: job.phase,
      expiresAt: job.expiresAt,
      progress: job.progress,
      space: job.planBytes,
      volumes: job.volumes,
      transfer: {
        hardlinkFiles: Object.values(job.files).filter((file) => file.mode === 'hardlink').length,
        copyFiles: Object.values(job.files).filter((file) => file.mode === 'copy').length,
        reusedFiles: Object.values(job.files).filter((file) => file.mode === 'reuse').length
      },
      results: job.results,
      items: job.items ?? null,
      download:
        job.kind === 'download'
          ? {
              bytes: downloaded,
              totalBytes: job.planBytes.download,
              bytesPerSecond: rate,
              remainingSeconds: rate
                ? Math.ceil(Math.max(0, job.planBytes.download - downloaded) / rate)
                : null,
              components: job.selected.map((item) => ({
                id: item.id,
                status: job.completed.includes(item.id)
                  ? 'ready'
                  : job.progress?.component === item.id
                    ? job.status === 'running'
                      ? job.phase
                      : job.status
                    : 'waiting',
                problem:
                  job.progress?.component === item.id && job.status === 'failed'
                    ? job.problem
                    : null
              }))
            }
          : null,
      warnings: job.warnings,
      problem: job.problem ?? null
    }
  }
  async save(job) {
    await writeJson(this.jobFile(job.id), job)
    this.bus.emit(job.id, this.snapshot(job))
  }
  async getJob(id) {
    const job = await this.readJob(id)
    requireValue(job, 'setup_job_missing')
    return this.snapshot(job)
  }
  async listJobs() {
    const result = []
    for (const name of await readdir(path.join(this.directory, 'jobs')))
      if (/^[a-zA-Z0-9-]+\.json$/.test(name))
        result.push(this.snapshot(await readJson(path.join(this.directory, 'jobs', name))))
    return result.sort((a, b) => b.expiresAt - a.expiresAt)
  }
  async observe(id, listener) {
    return this.bus.subscribe(id, listener, await this.getJob(id))
  }

  /** 持久计划身份同时承担重试去重；任务不依附 HTTP 或页面生命周期。
   * @param {string} id 已确认计划的持久身份。
   * @returns {Promise<import('../../shared/contracts/setup.js').SetupSnapshot>} 受理后的快照，任务在后端继续执行。
   */
  async start(id) {
    return this.queue.run('admission', async () => {
      requireValue(!this.closed, 'setup_closed')
      if (this.running.has(id)) return this.getJob(id)
      requireValue(this.running.size === 0, 'setup_busy', '已有资源准备任务正在执行。')
      const job = await this.readJob(id)
      requireValue(job, 'setup_job_missing')
      if (job.status === 'succeeded') return this.snapshot(job)
      requireValue(['planned', 'paused', 'failed'].includes(job.status), 'setup_job_not_resumable')
      requireValue(job.phase !== 'planning', 'setup_plan_incomplete')
      requireValue(
        job.status !== 'planned' || job.expiresAt > Date.now(),
        'setup_plan_expired',
        '计划已过期，请重新扫描或选择资源。'
      )
      try {
        job.volumes = await this.space(job)
      } catch (error) {
        job.status = 'failed'
        job.problem = problem(error)
        await this.save(job)
        throw error
      }
      job.status = 'running'
      job.problem = null
      await this.save(job)
      const controller = new AbortController()
      const active = { controller, action: 'pause', done: null }
      this.running.set(id, active)
      active.done = this.execute(job, controller.signal)
        .catch(async (error) => {
          job.status = controller.signal.aborted ? 'paused' : 'failed'
          job.problem = controller.signal.aborted ? null : problem(error)
          const item = job.items?.[job.currentItem]
          if (item) {
            item.status = job.status === 'paused' ? 'paused' : item.completed ? 'partial' : 'failed'
            item.problem = job.problem
          }
          await this.save(job)
          if (controller.signal.aborted && active.action === 'cancel') await this.cancel(job)
        })
        .catch((error) => {
          this.bus.emit(job.id, this.snapshot(job))
          this.assets.reportMaintenance(error)
        })
        .finally(() => this.running.delete(id))
      return this.snapshot(job)
    })
  }

  async controlJob(id, action) {
    requireValue(['pause', 'resume', 'cancel'].includes(action))
    if (action === 'resume') return this.start(id)
    return this.queue.run('admission', async () => {
      const active = this.running.get(id)
      if (active) {
        active.action = action
        active.controller.abort()
        await active.done
      } else if (action === 'cancel') {
        const job = await this.readJob(id)
        requireValue(job, 'setup_job_missing')
        await this.cancel(job)
      }
      return this.getJob(id)
    })
  }

  /** 结束引导绑定的导入，先核对真实提交，再释放剩余准备资源。 */
  async skipLegacy(id) {
    return this.queue.run('admission', async () => {
      requireValue(!this.closed, 'setup_closed')
      let job = await this.readJob(id)
      requireValue(job?.kind === 'legacy', 'invalid_onboarding_job')
      requireValue(job.status !== 'running', 'legacy_import_running')
      const active = this.running.get(id)
      if (active) {
        await active.done
        job = await this.readJob(id)
      }
      if (job.keptLegacy !== undefined) return job.keptLegacy
      if (job.status === 'succeeded') return true
      job.keptLegacy = await this.hasLegacyData(job)
      if (job.status === 'cancelled') await this.save(job)
      else await this.cancel(job)
      return job.keptLegacy
    })
  }

  async hasLegacyData(job) {
    const { scan, options, refs } = job
    const selection = options.include
    if (selection.characters) {
      for (const role of scan.roles) {
        const saved = await this.characters.get(role.id)
        if (saved?.extensions?.pythonImport?.source === scan.identity) return true
      }
      for (const persona of scan.personas ?? []) {
        const saved = await this.characters.getPersona(legacyPersonaId(persona))
        if (saved?.extensions?.pythonImport?.source === scan.identity) return true
      }
    }
    if (selection.chats) {
      for (const original of scan.chats) {
        const saved = await this.store.load(pythonChatId(original))
        if (saved.chat?.meta.extensions?.pythonImport?.source === scan.identity) return true
      }
    }
    if (selection.connections) {
      const { definitions, useBuiltin } = legacyConnections(scan.config)
      const ids = new Set(definitions.map(({ definition }) => definition.id))
      if (
        useBuiltin &&
        this.builtinConnection &&
        (job.connectionIds?.includes('builtin-up') || job.hadBuiltinConnection === false)
      )
        ids.add('builtin-up')
      if ((await this.settings.listConnections()).some((item) => ids.has(item.id))) return true
    }
    if (selection.gsv) {
      const record = (await this.installations())['gsv.models']
      if (record?.legacySource === scan.identity && Object.keys(record.refs).length) return true
      const candidates = new Set(Object.values(scan.gsv).map((key) => refs[key]?.id))
      const resources = await this.settings.get('speechStartup.gptSovits.resources')
      if (extractAssetReferences(resources).some((ref) => candidates.has(ref.id))) return true
    }
    // 兼容引入来源标记前，已经写入完成记录的任务。
    for (const item of job.results) {
      if (item.kind === 'character' && (await this.characters.get(item.id))) return true
      if (item.kind === 'chat' && (await this.store.load(item.id)).status === 'loaded') return true
    }
    return false
  }
  async cancel(job) {
    requireValue(job.status !== 'succeeded', 'setup_job_finished')
    if (job.status === 'cancelled') return
    const planning = job.phase === 'planning'
    if (job.kind === 'legacy' && job.status !== 'planned' && job.keptLegacy === undefined)
      job.keptLegacy = await this.hasLegacyData(job)
    job.status = 'cancelled'
    job.phase = 'cancelled'
    for (const item of Object.values(job.items ?? {}))
      if (['waiting', 'committing', 'paused'].includes(item.status))
        item.status = item.completed ? 'partial' : 'skipped'
    for (const [key, file] of Object.entries(job.files))
      if (file.purpose === 'model' && !job.refs[key])
        await rm(this.modelDestination(job.id, key, file.name), { force: true })
    job.refs = {}
    await this.save(job)
    await this.assets.releaseWhenPossible('setup:' + job.id)
    if (planning)
      await rm(path.join(this.library, 'models', 'setup', job.id), { recursive: true, force: true })
  }

  async execute(job, signal) {
    if (job.kind === 'legacy') await this.importLegacy(job, signal)
    else {
      job.downloaded = {}
      for (const file of downloadFiles(job.selected)) {
        const destination = path.join(this.cache, file.sha256)
        const cached = await stat(destination).catch((error) => {
          if (error.code !== 'ENOENT') throw error
          return null
        })
        if (cached?.size === file.sizeBytes) job.downloaded[file.sha256] = file.sizeBytes
        else {
          const partial = await stat(destination + '.part').catch((error) => {
            if (error.code !== 'ENOENT') throw error
            return null
          })
          if (partial?.size <= file.sizeBytes) job.downloaded[file.sha256] = partial.size
        }
      }
      for (const component of job.selected) {
        signal.throwIfAborted()
        if (job.completed.includes(component.id)) continue
        await this.installComponent(job, component, signal)
        job.completed.push(component.id)
        await this.enablePreparedVoices(job)
        await this.save(job)
      }
      await this.enablePreparedVoices(job)
    }
    signal.throwIfAborted()
    job.status = 'succeeded'
    job.phase = 'complete'
    job.progress = null
    await this.save(job)
  }

  async importFile(job, key, signal) {
    if (job.refs[key]) return job.refs[key]
    const file = job.files[key]
    const source = await sourcePath(job.scan.root, file.name)
    let asset
    if (file.purpose === 'model' && !file.entry) {
      const destination = this.modelDestination(job.id, key, file.name)
      if (file.mode === 'hardlink') {
        const held = await stamp(destination)
        requireValue(
          held.ino === file.stamp.ino &&
            held.dev === file.stamp.dev &&
            held.size === file.stamp.size &&
            held.mtime === file.stamp.mtime,
          'legacy_source_changed'
        )
      } else {
        await rm(destination, { force: true })
        await copyNew(source, destination, signal)
      }
      signal.throwIfAborted()
      const name = path
        .relative(path.join(this.library, 'models'), destination)
        .split(path.sep)
        .join('/')
      asset = (await this.assets.registerModelFiles([name], 'setup:' + job.id))[name]
    } else {
      const imported = await this.assets.importAsset(
        file.entry
          ? { kind: 'local-directory', path: source, entry: file.entry }
          : { kind: 'local-file', path: source },
        'setup:' + job.id,
        file.purpose,
        signal
      )
      asset = imported.asset
    }
    if (!file.entry)
      requireValue(
        fingerprint(await stamp(source)) === fingerprint(file.stamp),
        'legacy_source_changed'
      )
    job.refs[key] = asset
    await this.save(job)
    const sourceFile = path.join(this.directory, 'sources', job.scan.identity + '.json')
    const sources = await readJson(sourceFile, {})
    sources[key] = { fingerprint: fingerprint(file.stamp ?? file.stamps), ref: asset }
    await writeJson(sourceFile, sources)
    return asset
  }

  async importLegacy(job, signal) {
    job.phase = 'validating'
    await this.save(job)
    await validateLegacySource(job.scan)
    let completed = 0
    for (const key of Object.keys(job.files)) {
      signal.throwIfAborted()
      job.phase = 'importing'
      job.progress = { completed, total: Object.keys(job.files).length }
      await this.importFile(job, key, signal)
      completed += 1
    }
    await validateLegacySource(job.scan)
    job.phase = 'committing'
    await this.save(job)
    job.items ??= Object.fromEntries(
      legacyGroups.map((key) => [
        key,
        {
          total: legacySummary(job.scan, this.builtinConnection)[key],
          completed: 0,
          status: job.options.include[key] ? 'waiting' : 'skipped'
        }
      ])
    )
    const mapping = Object.create(null)
    const namesInUse = new Set((await this.characters.list()).map((item) => item.name.trim()))
    if (job.options.include.characters) {
      await this.updateLegacyItem(job, 'characters', 'committing', 0)
      for (const role of job.scan.roles) {
        signal.throwIfAborted()
        if (await this.characters.get(role.id)) {
          await this.updateLegacyItem(
            job,
            'characters',
            'committing',
            job.items.characters.completed + 1
          )
          continue
        }
        const original = role.name.trim()
        let name = original
        for (let number = 1; namesInUse.has(name); number += 1) name = `${original}_${number}`
        await this.characters.save(characterDefinition(role, job.refs, name, job.scan.identity))
        namesInUse.add(name)
        job.results.push({ kind: 'character', id: role.id, status: 'imported' })
        await this.updateLegacyItem(
          job,
          'characters',
          'committing',
          job.items.characters.completed + 1
        )
      }
    }
    for (const role of job.scan.roles) mapping[role.name] = role.id
    for (const [name, id] of Object.entries({ 爱音: 'anon', 素世: 'soyo', 祥子: 'sakiko' }))
      mapping[name] ??= (await this.characters.list()).find((role) => role.name === name)?.id ?? id
    if (job.options.include.gsv) {
      await this.updateLegacyItem(job, 'gsv', 'committing', 0)
      const refs = Object.fromEntries(
        Object.entries(job.scan.gsv)
          .filter(([, key]) => job.refs[key])
          .map(([name, key]) => [name, job.refs[key]])
      )
      if (Object.keys(refs).length) {
        await bindGsv(this.settings, refs)
        const resources = await this.settings.get('speechStartup.gptSovits.resources')
        const complete =
          ['v4', 'v2ProPlus'].every(
            (version) => selectGptSovitsFiles(resources, version).missing.length === 0
          ) &&
          Object.keys(refs).some((name) => name.includes('nltk_data/')) &&
          Object.keys(refs).some((name) => name.endsWith('open_jtalk_dic_utf_8-1.11/sys.dic'))
        const record = {
          id: 'gsv.models',
          status: complete ? 'ready' : 'partial',
          legacySource: job.scan.identity,
          version: 'legacy',
          refs,
          directory: path.join(this.library, 'models', 'setup', job.id, 'legacy', 'GPT_SoVITS')
        }
        await this.activate(record)
      }
      const record = (await this.installations())['gsv.models']
      await this.updateLegacyItem(
        job,
        'gsv',
        record?.status === 'partial' && Object.keys(refs).length ? 'partial' : 'ready',
        Object.keys(refs).length
      )
    }
    if (job.options.include.connections && !job.completed.includes('connections')) {
      job.hadBuiltinConnection ??= (await this.settings.listConnections()).some(
        (item) => item.id === 'builtin-up'
      )
      await this.updateLegacyItem(job, 'connections', 'committing', job.connectionIds?.length ?? 0)
      const imported = await importConnections(
        this.settings,
        job.scan.config,
        this.builtinConnection,
        async (id) => {
          job.connectionIds ??= []
          if (!job.connectionIds.includes(id)) {
            job.connectionIds.push(id)
            await this.updateLegacyItem(job, 'connections', 'committing', job.connectionIds.length)
          }
        }
      )
      job.pendingConnections = imported.pending
      if (imported.pending.length)
        job.warnings.push({ code: 'legacy_connections_pending', count: imported.pending.length })
      job.completed.push('connections')
      await this.updateLegacyItem(
        job,
        'connections',
        imported.pending.length ? 'partial' : 'ready',
        job.connectionIds?.length ?? 0
      )
    }
    if (job.options.include.characters) {
      await this.updateLegacyItem(job, 'characters', 'committing', job.items.characters.completed)
      const names = job.scan.config?.character_setting?.character_order?.character_names ?? []
      const order = [
        ...new Set([
          ...names.map((name) => mapping[name]).filter(Boolean),
          ...(await this.characters.list()).map((item) => item.id)
        ])
      ]
      if (order.length) await this.characters.setOrder(order)
      for (const persona of job.scan.personas ?? []) {
        signal.throwIfAborted()
        const id = legacyPersonaId(persona)
        if (await this.characters.getPersona(id)) {
          await this.updateLegacyItem(
            job,
            'characters',
            'committing',
            job.items.characters.completed + 1
          )
          continue
        }
        const roleId =
          persona.user_as_character_folder_name || mapping[persona.user_as_character_name]
        const role = roleId ? await this.characters.get(roleId) : null
        const source = role
          ? { kind: 'character', characterId: role.id, formId: role.defaultFormId }
          : {
              kind: 'custom',
              displayName: persona.name ?? 'User',
              description: persona.description ?? '',
              avatar: job.refs[persona.avatar] ?? null
            }
        await this.characters.savePersona({
          id,
          revision: 0,
          source,
          extensions: { pythonImport: { source: job.scan.identity } }
        })
        await this.updateLegacyItem(
          job,
          'characters',
          'committing',
          job.items.characters.completed + 1
        )
      }
      await this.updateLegacyItem(job, 'characters', 'ready', job.items.characters.completed)
    }
    if (job.options.include.chats) {
      await this.updateLegacyItem(job, 'chats', 'committing', 0)
      const imported = await this.importChats(job.scan, job.refs, mapping, signal, async (chat) => {
        job.results.push({ kind: 'chat', id: chat.id, status: 'imported' })
        await this.updateLegacyItem(job, 'chats', 'committing', job.items.chats.completed + 1)
      })
      job.warnings.push(...imported.warnings)
      await this.updateLegacyItem(
        job,
        'chats',
        imported.warnings.length ? 'partial' : 'ready',
        job.items.chats.total
      )
    }
  }

  async updateLegacyItem(job, key, status, completed) {
    job.currentItem = key
    job.items[key] = { ...job.items[key], status, completed, problem: null }
    await this.save(job)
  }

  /** 当前安装记录与角色形态同时可用时才接受自动启用意图。 */
  async enablePreparedVoices(job) {
    if (job.voicePreferenceRevision === undefined) return
    const installed = await this.installations()
    for (const id of job.options.selection.filter((choice) => choice.startsWith('voice.'))) {
      const component = job.selected.find((item) => item.id === id)
      if (component && !job.completed.includes(id)) continue
      const role = await this.voiceTarget(id.slice('voice.'.length))
      if (!role) continue
      const characterId = role.id
      const ready = await Promise.all(
        role.forms.map((form) => this.speechReady(characterId, form.voice, installed))
      )
      if (ready.some(Boolean))
        await this.settings.enablePreparedVoice(characterId, job.voicePreferenceRevision)
    }
  }

  /** 判定已安装的语音材料能否供指定形态使用。 */
  async speechReady(characterId, voice, installed = null) {
    installed ??= await this.installations()
    const records = Object.values(installed)
    const voicePackage = records.find((item) => item.characterId === characterId)
    if (installed['gsv.runtime']?.status !== 'ready' || installed['gsv.models']?.status !== 'ready')
      return false
    const resources = await this.settings.get('speechStartup.gptSovits.resources')
    const versions = voicePackage?.architecture ? [voicePackage.architecture] : ['v4', 'v2ProPlus']
    if (!versions.some((version) => selectGptSovitsFiles(resources, version).missing.length === 0))
      return false
    try {
      const executable = installed['gsv.runtime'].runtime?.pythonExecutable
      if (!executable || !(await stat(executable)).isFile()) return false
      for (const file of Object.values(installed['gsv.runtime'].runtime)) await stat(file)
      for (const ref of extractAssetReferences(installed['gsv.models'].refs))
        await this.assets.localPath(ref)
      return this.voiceReady(voice)
    } catch {
      return false
    }
  }

  /** 返回主页所需的资源状态，语音识别与角色语音独立。 */
  async getFeatureState() {
    const installed = await this.installations()
    let asrReady =
      (await this.componentReady({ id: 'asr.runtime' }, installed)) &&
      (await this.componentReady({ id: 'asr.models' }, installed))
    if (this.localRecognition) {
      const files = ['encoder_model_quantized.onnx', 'decoder_model_merged_quantized.onnx']
      const checks = await Promise.allSettled(
        files.map((file) => stat(path.join(this.localRecognition, 'onnx', file)))
      )
      asrReady = checks.every((check) => check.status === 'fulfilled' && check.value.isFile())
    }
    const preference = await this.settings.voicePreference()
    const characters = []
    for (const summary of await this.characters.list()) {
      const role = await this.characters.get(summary.id)
      if (!role) continue
      for (const form of role.forms) {
        const ready = await this.speechReady(role.id, form.voice, installed)
        characters.push({
          characterId: role.id,
          formId: form.id,
          ready,
          autoEnabled: ready && preference.enabled[role.id] === true
        })
      }
    }
    return { asrReady, characters }
  }

  async installComponent(job, component, signal) {
    job.phase = 'installing'
    job.progress = { component: component.id, completed: 0, total: 0 }
    await this.save(job)
    const base = path.join(
      this.library,
      'models',
      'setup',
      job.id,
      component.id.replaceAll('.', '-')
    )
    const runtime = component.kind === 'runtime'
    const destination = runtime ? path.join(this.library, 'runtimes', job.id, component.id) : base
    let prepared = job.prepared?.[component.id]?.refs ?? copy(component.reused ?? {})
    // 候选目录尚未启用，可以整体重建；已完成组件由 completed 日志跳过。
    if (!job.prepared?.[component.id]) {
      await rm(destination, { recursive: true, force: true })
      await mkdir(destination, { recursive: true })
      for (const file of component.files) {
        signal.throwIfAborted()
        job.phase = 'downloading'
        job.progress = {
          component: component.id,
          completed: job.downloaded[file.sha256] ?? 0,
          total: file.sizeBytes
        }
        job.downloadRate = null
        await this.save(job)
        let last = Date.now()
        let sampledBytes = null
        const cached = await downloadFile(file, this.cache, {
          fetch: this.transport,
          signal,
          progress: (bytes) => {
            const now = Date.now()
            job.downloaded[file.sha256] = bytes
            job.progress.completed = bytes
            if (sampledBytes === null) sampledBytes = bytes
            if (now - last < 250) return
            job.downloadRate =
              Math.max(0, Math.round(((bytes - sampledBytes) * 1000) / (now - last))) || null
            last = now
            sampledBytes = bytes
            this.bus.emit(job.id, this.snapshot(job))
          }
        }).catch(async (error) => {
          const partial = await stat(path.join(this.cache, file.sha256 + '.part')).catch(() => null)
          job.downloaded[file.sha256] = Math.min(file.sizeBytes, partial?.size ?? 0)
          job.progress.completed = job.downloaded[file.sha256]
          throw error
        })
        job.downloaded[file.sha256] = file.sizeBytes
        job.progress.completed = file.sizeBytes
        job.phase = 'installing'
        await this.save(job)
        if (file.archive)
          await extractArchive(cached, destination, file.archive, file.expandedBytes, signal)
        else {
          await mkdir(path.dirname(path.join(destination, file.path)), { recursive: true })
          await copyNew(cached, path.join(destination, file.path), signal)
        }
      }
      let runtimeInfo = null
      if (runtime) runtimeInfo = await prepareRuntime(component, destination, signal)
      else {
        const names = await walk(destination)
        if (component.kind === 'asr')
          for (const required of [
            'config.json',
            'tokenizer.json',
            'tokenizer_config.json',
            'preprocessor_config.json',
            'generation_config.json',
            'onnx/encoder_model_quantized.onnx',
            'onnx/decoder_model_merged_quantized.onnx'
          ])
            requireValue(
              names.includes(required),
              'asr_package_incomplete',
              '语音识别模型包缺少必要文件。'
            )
        const modelNames = []
        for (const name of names) {
          const file = path.join(destination, name)
          if (component.kind === 'voice' && /\.(wav|mp3)$/i.test(name)) {
            prepared[name] = (
              await this.assets.importAsset(
                { kind: 'local-file', path: file },
                'setup:' + job.id,
                'audio',
                signal
              )
            ).asset
          } else
            modelNames.push(
              path.relative(path.join(this.library, 'models'), file).split(path.sep).join('/')
            )
        }
        const registered = await this.assets.registerModelFiles(modelNames, 'setup:' + job.id)
        for (const [name, ref] of Object.entries(registered))
          prepared[
            path
              .relative(destination, path.join(this.library, 'models', name))
              .split(path.sep)
              .join('/')
          ] = ref
        Object.assign(
          job.refs,
          Object.fromEntries(
            Object.entries(prepared).map(([key, value]) => [component.id + ':' + key, value])
          )
        )
        await this.save(job)
      }
      job.prepared ??= {}
      job.prepared[component.id] = { refs: prepared, runtime: runtimeInfo }
      await this.save(job)
    }
    prepared = job.prepared[component.id].refs
    if (component.kind === 'voice') {
      // 音频已经交给资源库并持久登记，候选目录里的副本不再需要。
      for (const name of Object.keys(prepared))
        if (/\.(wav|mp3)$/i.test(name)) await rm(path.join(destination, name), { force: true })
    }
    if (component.kind === 'gsv') await bindGsv(this.settings, prepared)
    const voice =
      component.kind === 'voice' ? await this.bindVoice(component, prepared, destination) : null
    const record = {
      id: component.id,
      status: 'ready',
      version: component.version ?? '1',
      target: component.target ?? null,
      refs: prepared,
      directory: destination,
      runtime: job.prepared[component.id].runtime,
      ...voice
    }
    await this.activate(record)
    if (component.id === 'gsv.runtime')
      await this.settings.update({
        speechStartup: {
          gptSovits: {
            device: component.target.endsWith('-cuda') ? 'cuda' : 'cpu',
            precision: 'float32'
          }
        }
      })
    job.results.push({ kind: 'component', id: component.id, status: 'installed' })
  }

  async activate(record) {
    await this.queue.run('installations', async () => {
      const installed = await this.installations()
      const previous = installed[record.id]
      if (record.runtime && previous?.directory && previous.directory !== record.directory) {
        const file = path.join(this.directory, 'retired-runtimes.json')
        const retired = await readJson(file, [])
        if (!retired.includes(previous.directory)) {
          retired.push(previous.directory)
          await writeJson(file, retired)
        }
      }
      installed[record.id] = record
      await this.assets.commitReferences('installation:' + record.id, record, () =>
        writeJson(path.join(this.directory, 'installations.json'), installed)
      )
    })
  }

  /** 已导入的 GSV 文件按相对用途复用；只有缺失的原始文件或压缩包进入下载计划。 */
  async reuseGsv(component, installed, job) {
    const existing = {}
    for (const [name, ref] of Object.entries(installed?.refs ?? {})) {
      const normalized = name
        .replace(/^GPT_SoVITS\//, '')
        .replace(/^text\/G2PWModel\//, 'G2PWModel/')
      try {
        await this.assets.localPath(ref)
        existing[normalized] = { ref, info: await this.assets.inspect(ref) }
      } catch {
        /* 文件离线时重新准备。 */
      }
    }
    component.reused = {}
    const missing = []
    for (const file of component.files) {
      const members = file.archive ? file.members : [{ path: file.path, sizeBytes: file.sizeBytes }]
      if (
        !members?.length ||
        !members.every((item) => existing[item.path]?.info.sizeBytes === item.sizeBytes)
      ) {
        missing.push(file)
        continue
      }
      for (const item of members) component.reused[item.path] = existing[item.path].ref
    }
    component.files = missing
    const references = Object.values(component.reused)
    if (references.length && job) {
      await this.assets.addReferences('setup:' + job.id, references)
      Object.assign(
        job.refs,
        Object.fromEntries(
          Object.entries(component.reused).map(([name, ref]) => ['gsv:' + name, ref])
        )
      )
    }
  }

  async bindVoice(component, refs, directory) {
    const voice = await readJson(path.join(directory, component.characterId, 'voice.json'))
    requireValue(
      voice?.schemaVersion === 1 &&
        voice.characterId === component.characterId &&
        ['v4', 'v2ProPlus'].includes(voice.architecture),
      'voice_manifest_invalid'
    )
    const role = component.targetCharacterId
      ? await this.characters.get(component.targetCharacterId)
      : await this.voiceTarget(component.characterId)
    requireValue(role, 'character_missing', '请先完成角色初始化，再下载角色语音。')
    const prefix = component.characterId + '/'
    function asset(name) {
      requireValue(
        typeof name === 'string' && Object.hasOwn(refs, prefix + name),
        'voice_manifest_invalid'
      )
      return refs[prefix + name]
    }
    function reference(value) {
      requireValue(
        typeof value?.text === 'string' && typeof value.language === 'string',
        'voice_manifest_invalid'
      )
      return { ...value, audio: asset(value.audio) }
    }
    for (const form of role.forms) {
      const definition = voice.forms[form.id] ?? voice.forms.default
      if (!definition || (await this.voiceReady(form.voice))) continue
      form.voice = {
        gptWeights: asset(voice.weights.gpt),
        sovitsWeights: asset(voice.weights.sovits),
        defaultReference: reference(definition.defaultReference),
        emotions: Object.fromEntries(
          Object.entries(definition.emotions ?? {}).map(([name, value]) => [name, reference(value)])
        )
      }
    }
    await this.characters.save(role)
    return { characterId: role.id, architecture: voice.architecture }
  }

  async voiceTarget(characterId) {
    const canonical = await this.characters.get(characterId)
    if (canonical) return canonical
    const displayName = setupGroups
      .find((group) => group.id === 'voices')
      .options.find((option) => option.id === 'voice.' + characterId)?.name
    const summary = (await this.characters.list()).find((role) => role.name === displayName)
    return summary ? this.characters.get(summary.id) : null
  }

  async voiceReady(voice) {
    if (!voice?.gptWeights || !voice.sovitsWeights || !voice.defaultReference?.audio) return false
    try {
      for (const ref of [voice.gptWeights, voice.sovitsWeights, voice.defaultReference.audio])
        await this.assets.localPath(ref)
      return true
    } catch {
      return false
    }
  }

  /** 运行进程持有环境租约；更新只切换记录，不删除仍被进程使用的旧路径。 */
  async acquireRuntime(kind) {
    return this.queue.run('installations', () => this.acquireRuntimeUnlocked(kind))
  }

  async acquireRuntimeUnlocked(kind) {
    const installed = await this.installations()
    const runtime = installed[kind + '.runtime']
    const model = installed[kind + '.models']
    requireValue(
      runtime?.status === 'ready' && model?.status === 'ready',
      'runtime_not_installed',
      '请先下载并安装语音资源。'
    )
    const owner = await this.assets.createTemporaryOwner(
      'setup-runtime',
      extractAssetReferences(model.refs)
    )
    try {
      const environment = {}
      if (kind === 'gsv') {
        if (runtime.runtime.ffmpegExecutable)
          environment.PATH =
            path.dirname(runtime.runtime.ffmpegExecutable) +
            path.delimiter +
            (process.env.PATH ?? '')
        const dict = Object.entries(model.refs).find(([name]) =>
          name.endsWith('open_jtalk_dic_utf_8-1.11/sys.dic')
        )
        if (dict)
          environment.OPEN_JTALK_DICT_DIR = path.dirname(await this.assets.localPath(dict[1]))
        const nltk = Object.entries(model.refs).find(([name]) => name.includes('nltk_data/'))
        if (nltk) {
          const file = await this.assets.localPath(nltk[1])
          const parts = nltk[0].split('/')
          environment.NLTK_DATA = path.resolve(
            file,
            ...Array(parts.length - parts.indexOf('nltk_data') - 1).fill('..')
          )
        }
      }
      const leaseFile = path.join(this.directory, 'runtime-leases.json')
      const leases = await readJson(leaseFile, {})
      leases[owner] = { directory: runtime.directory, processId: null }
      await writeJson(leaseFile, leases)
      return {
        ...runtime.runtime,
        environment,
        modelDirectory: model.directory,
        bindResources: (pid, group) =>
          this.queue.run('installations', async () => {
            await this.assets.bindOwnerProcess(owner, pid, group)
            const leases = await readJson(leaseFile, {})
            leases[owner] = {
              directory: runtime.directory,
              processId: pid,
              processGroupId: group ?? null
            }
            await writeJson(leaseFile, leases)
          }),
        releaseResources: async (pid = null) => {
          await this.queue.run('installations', async () => {
            if (!(await this.assets.releaseProcessOwner(owner, pid))) return
            const leases = await readJson(leaseFile, {})
            delete leases[owner]
            await writeJson(leaseFile, leases)
          })
          await this.maintainRuntimes()
        }
      }
    } catch (error) {
      await this.assets.releaseProcessOwner(owner)
      throw error
    }
  }

  /** 只回收已替换且确认没有进程持有的环境；不确定的进程记录继续保留。 */
  async maintainRuntimes() {
    return this.queue.run('installations', async () => {
      const leaseFile = path.join(this.directory, 'runtime-leases.json')
      const leases = await readJson(leaseFile, {})
      for (const [owner, lease] of Object.entries(leases))
        if (lease.processId && !processUsesResources(lease)) delete leases[owner]
      await writeJson(leaseFile, leases)
      const installed = await this.installations()
      const protectedDirectories = new Set([
        ...Object.values(installed).map((item) => item.directory),
        ...Object.values(leases).map((item) => item.directory)
      ])
      const retiredFile = path.join(this.directory, 'retired-runtimes.json')
      const pending = []
      for (const directory of await readJson(retiredFile, [])) {
        if (protectedDirectories.has(directory)) {
          pending.push(directory)
          continue
        }
        const relative = path.relative(this.library, directory).split(path.sep).join('/')
        if (!/^runtimes\/[a-f0-9-]{36}\/(asr|gsv)\.runtime$/.test(relative)) {
          pending.push(directory)
          continue
        }
        try {
          await rm(await sourcePath(this.library, relative), { recursive: true, force: true })
        } catch (error) {
          if (error.code !== 'ENOENT') {
            pending.push(directory)
            this.assets.reportMaintenance(error)
          }
        }
      }
      await writeJson(retiredFile, pending)
    })
  }

  async maintain() {
    await this.queue.run('admission', async () => {
      for (const snapshot of await this.listJobs())
        if (snapshot.status === 'planned' && snapshot.expiresAt < Date.now())
          await this.cancel(await this.readJob(snapshot.id))
    })
    await this.maintainRuntimes()
  }

  async close() {
    this.closed = true
    await this.queue.run('admission', async () => {})
    for (const active of this.running.values()) active.controller.abort()
    await Promise.all([...this.running.values()].map((active) => active.done))
    this.bus.clear()
  }
}
