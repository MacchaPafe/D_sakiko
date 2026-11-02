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
import { bindGsv, characterDefinition, importConnections } from './bindings.js'
import { selectGptSovitsFiles } from '../../shared/contracts/speech-resources.js'

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
      hasConnections: !!scan.config?.llm_setting,
      gsvFileCount: Object.keys(scan.gsv).length,
      warnings: scan.warnings,
      logicalBytes: Object.values(scan.files).reduce((sum, file) => sum + file.sizeBytes, 0)
    }
  }

  async componentReady(component, installed) {
    if (component.kind === 'voice') {
      const role = await this.characters.get(component.characterId)
      if (role?.forms.length && role.forms.every((form) => form.voice)) {
        const usable = await Promise.all(role.forms.map((form) => this.voiceReady(form.voice)))
        if (usable.every(Boolean)) return true
      }
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
   * @param {{refresh?: boolean, accelerator?: 'cpu'|'cuda'}} options 是否刷新固定声明及目标加速方式。
   * @returns {Promise<object>} 三组资源的可用性、已准备状态和空间提示。
   */
  async getCatalog(options = {}) {
    const components = await this.catalog.components(options)
    const installed = await this.installations()
    const groups = copy(setupGroups)
    for (const group of groups)
      for (const option of group.options) {
        const selected = await this.catalog.select([option.id], { ...options, refresh: false })
        const missing = []
        for (const component of selected)
          if (!(await this.componentReady(component, installed))) {
            if (component.id === 'gsv.models')
              await this.reuseGsv(component, installed['gsv.models'])
            missing.push(component)
          }
        option.ready = missing.length === 0
        option.available = missing.every((item) => item.available)
        option.sizeKnown = missing.every((item) => item.available)
        option.unavailableReason =
          missing.find((item) => !item.available)?.unavailableReason ?? null
        option.downloadBytes = missing.reduce(
          (sum, item) => sum + item.files.reduce((size, file) => size + file.sizeBytes, 0),
          0
        )
        if (group.id === 'voices') option.requiresSelection = 'gsv'
      }
    return {
      groups,
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
        job.options.include = selection
        const keys = new Set()
        if (selection.characters) {
          for (const persona of scan.personas ?? []) if (persona.avatar) keys.add(persona.avatar)
          const existingRoles = await this.characters.list()
          for (const role of scan.roles) {
            if (existingRoles.some((item) => item.id === role.id || item.name === role.name))
              continue
            for (const form of role.forms) {
              for (const model of form.models) keys.add(model.key)
            }
            for (const key of Object.values(role.voiceFiles)) if (key) keys.add(key)
            if (role.avatar) keys.add(role.avatar)
          }
          // 外部引用参考音频也属于角色资料，不要求与角色目录相邻。
          for (const role of scan.roles.filter(
            (role) => !existingRoles.some((item) => item.id === role.id || item.name === role.name)
          ))
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
        for (const component of await this.catalog.select(options.selection, {
          accelerator: options.accelerator ?? 'cpu'
        })) {
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
            job.planBytes.download += file.sizeBytes
            job.planBytes.added += file.expandedBytes ?? file.sizeBytes
            job.planBytes.logical += file.expandedBytes ?? file.sizeBytes
          }
          job.planBytes.added += component.installBytes ?? 0
        }
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
    for (const component of job.selected.filter(
      (item) => !job.completed.includes(item.id) && !job.prepared?.[item.id]
    )) {
      library += component.installBytes ?? 0
      for (const file of component.files) {
        library += file.expandedBytes ?? file.sizeBytes
        if (component.kind === 'voice' && /\.(wav|mp3)$/i.test(file.path)) library += file.sizeBytes
        const existing = await stat(path.join(this.cache, file.sha256)).catch(() => null)
        if (existing?.size !== file.sizeBytes) cache += file.sizeBytes
      }
    }
    return checkSpace([
      { directory: this.library, bytes: Math.max(0, library) },
      { directory: this.directory, bytes: application },
      { directory: this.cache, bytes: cache }
    ])
  }
  snapshot(job) {
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
      await this.space(job)
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
  }
  async cancel(job) {
    requireValue(job.status !== 'succeeded', 'setup_job_finished')
    const planning = job.phase === 'planning'
    job.status = 'cancelled'
    job.phase = 'cancelled'
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
    else
      for (const component of job.selected) {
        signal.throwIfAborted()
        if (job.completed.includes(component.id)) continue
        await this.installComponent(job, component, signal)
        job.completed.push(component.id)
        await this.save(job)
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
    const mapping = Object.create(null)
    if (job.options.include.characters)
      for (const role of job.scan.roles) {
        signal.throwIfAborted()
        const existing = (await this.characters.list()).find(
          (item) => item.id === role.id || item.name === role.name
        )
        if (existing) {
          mapping[role.id] = existing.id
          continue
        }
        await this.characters.save(characterDefinition(role, job.refs))
        mapping[role.id] = role.id
        job.results.push({ kind: 'character', id: role.id, status: 'imported' })
        await this.save(job)
      }
    for (const role of job.scan.roles)
      mapping[role.name] =
        mapping[role.id] ??
        (await this.characters.list()).find(
          (item) => item.id === role.id || item.name === role.name
        )?.id ??
        role.id
    for (const [name, id] of Object.entries({ 爱音: 'anon', 素世: 'soyo', 祥子: 'sakiko' }))
      mapping[name] ??= (await this.characters.list()).find((role) => role.name === name)?.id ?? id
    if (job.options.include.gsv) {
      const refs = Object.fromEntries(
        Object.entries(job.scan.gsv)
          .filter(([, key]) => job.refs[key])
          .map(([name, key]) => [name, job.refs[key]])
      )
      await bindGsv(this.settings, refs)
      const resources = await this.settings.get('speechStartup.gptSovits.resources')
      if (Object.keys(refs).length) {
        const complete =
          ['v4', 'v2ProPlus'].every(
            (version) => selectGptSovitsFiles(resources, version).missing.length === 0
          ) &&
          Object.keys(refs).some((name) => name.includes('nltk_data/')) &&
          Object.keys(refs).some((name) => name.endsWith('open_jtalk_dic_utf_8-1.11/sys.dic'))
        const record = {
          id: 'gsv.models',
          status: complete ? 'ready' : 'partial',
          version: 'legacy',
          refs,
          directory: path.join(this.library, 'models', 'setup', job.id, 'legacy', 'GPT_SoVITS')
        }
        await this.activate(record)
      }
    }
    if (job.options.include.connections && !job.completed.includes('connections')) {
      const imported = await importConnections(
        this.settings,
        job.scan.config,
        this.builtinConnection
      )
      job.pendingConnections = imported.pending
      if (imported.pending.length)
        job.warnings.push({ code: 'legacy_connections_pending', count: imported.pending.length })
      job.completed.push('connections')
      await this.save(job)
    }
    if (job.options.include.characters) {
      const names = job.scan.config?.character_setting?.character_order?.character_names ?? []
      const order = [
        ...new Set([
          ...names.map((name) => mapping[name]).filter(Boolean),
          ...(await this.characters.list()).map((item) => item.id)
        ])
      ]
      if (order.length) await this.characters.setOrder(order)
      for (const persona of job.scan.personas ?? []) {
        const id = 'legacy-persona-' + fingerprint(persona.persona_id ?? persona).slice(0, 24)
        if (await this.characters.getPersona(id)) continue
        const roleId =
          mapping[persona.user_as_character_folder_name] ?? mapping[persona.user_as_character_name]
        const role = roleId ? await this.characters.get(roleId) : null
        const source = role
          ? { kind: 'character', characterId: role.id, formId: role.defaultFormId }
          : {
              kind: 'custom',
              displayName: persona.name ?? 'User',
              description: persona.description ?? '',
              avatar: job.refs[persona.avatar] ?? null
            }
        await this.characters.savePersona({ id, revision: 0, source })
      }
    }
    if (job.options.include.chats) {
      const imported = await this.importChats(job.scan, job.refs, mapping, signal)
      job.warnings.push(...imported.warnings)
      job.results.push(
        ...imported.chats.map((chat) => ({ kind: 'chat', id: chat.id, status: 'imported' }))
      )
      await this.save(job)
    }
  }

  async installComponent(job, component, signal) {
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
        job.progress = { component: component.id, completed: 0, total: file.sizeBytes }
        await this.save(job)
        let last = 0
        const cached = await downloadFile(file, this.cache, {
          fetch: this.transport,
          signal,
          progress: (bytes) => {
            if (Date.now() - last > 250) {
              last = Date.now()
              job.progress.completed = bytes
              this.bus.emit(job.id, this.snapshot(job))
            }
          }
        })
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
    if (component.kind === 'voice') await this.bindVoice(component, prepared, destination)
    const record = {
      id: component.id,
      status: 'ready',
      version: component.version ?? '1',
      target: component.target ?? null,
      refs: prepared,
      directory: destination,
      runtime: job.prepared[component.id].runtime
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
    const role = await this.characters.get(component.characterId)
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
