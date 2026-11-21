/**
 * @typedef {import('../../../../shared/contracts/setup.js').SetupSnapshot} SetupSnapshot
 * @typedef {{id: string, name: string, ready: boolean, available: boolean, sizeKnown: boolean, downloadBytes: number, requiresSelection?: string|null, unavailableReason: string|null}} ResourceOption
 * @typedef {{groups: Array<{id: string, options: ResourceOption[]}>, selectionDownloadBytes: number|null}} ResourceCatalog
 * @typedef {object} SetupFlowState
 * @property {'loading'|'welcome'|'legacy'|'import'|'resources'|'download'|'home'|'error'} view 当前页面；快速开始返回前需确认后端撤回准备身份。
 * @property {{sourceId: string, name: string}|null} legacySource 宿主授权的旧版目录。
 * @property {object|null} legacyScan 扫描摘要，不包含来源文件内容或凭据。
 * @property {Record<string, boolean>} legacyInclude 整类导入选择。
 * @property {boolean} scanning 正在扫描，允许更换目录使旧结果失效。
 * @property {boolean} picking 正在等待系统选择器。
 * @property {import('../../../../shared/contracts/setup.js').OnboardingState|null} onboarding 后端初始化状态。
 * @property {ResourceCatalog|null} catalog 后端资源目录与去重估计。
 * @property {ResourceCatalog|null} managedCatalog 设置页上次可用的资源快照。
 * @property {boolean} managedCatalogChecking 设置页正在读取本地或远端状态。
 * @property {string|null} managedCatalogProblem 上次资源核对失败原因。
 * @property {string[]} selection 本次选择，默认空。
 * @property {SetupSnapshot|null} job 当前持久任务。
 * @property {object|null} features 真实功能就绪状态。
 * @property {string|null} pending 正在受理的用户操作。
 * @property {boolean} catalogLoading 容量估计尚未更新。
 * @property {boolean} refreshing 正在检查远端发布目录。
 * @property {boolean} connected 观察通道是否连接。
 * @property {string|null} connectionProblem 只有连接实际失败才提示，初次握手不显示断线。
 * @property {string|null} problem 操作失败原因。
 * @property {string|null} catalogProblem 目录读取失败原因。
 */

const managedRefreshIntervalMs = 10 * 60 * 1000

/** 应用级初始化与下载会话；React 卸载只断开观察，不撤销后端任务。 */
export class SetupFlow {
  /** @param {{connect: () => Promise<import('../../backend/client.js').BackendClient>, selectLegacyDirectory?: () => Promise<{sourceId: string, name: string}|null>, randomId?: () => string}} options 传输连接及可验证的命令身份。 */
  constructor({ connect, selectLegacyDirectory, randomId = () => crypto.randomUUID() }) {
    this.connect = connect
    this.randomId = randomId
    this.selectLegacyDirectory = selectLegacyDirectory
    this.scanRevision = 0
    this.pickerRevision = 0
    this.listeners = new Set()
    this.generation = 0
    this.catalogRevision = 0
    this.jobRevision = 0
    this.featureRevision = 0
    this.managedRevision = 0
    this.managedCheckedAt = null
    this.managedRequest = null
    /** @type {SetupFlowState} */
    this.state = {
      view: 'loading',
      onboarding: null,
      catalog: null,
      managedCatalog: null,
      managedCatalogChecking: false,
      managedCatalogProblem: null,
      selection: [],
      job: null,
      features: null,
      pending: null,
      catalogLoading: false,
      refreshing: false,
      connected: true,
      connectionProblem: null,
      problem: null,
      catalogProblem: null,
      legacySource: null,
      legacyScan: null,
      legacyInclude: {},
      scanning: false,
      picking: false
    }
  }
  getSnapshot = () => this.state
  subscribe = (listener) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  update(next) {
    this.state = { ...this.state, ...next }
    for (const listener of this.listeners) listener()
  }
  current(generation) {
    return this.active && generation === this.generation
  }

  /** @returns {Promise<void>} 读取持久状态；中断下载保留暂停，内置角色准备可幂等继续。 */
  mount = async () => {
    const generation = ++this.generation
    this.active = true
    this.update({ view: 'loading', pending: null, problem: null })
    try {
      const client = await this.connect()
      if (!this.current(generation)) {
        client.close()
        return
      }
      this.client = client
      let [onboarding, jobs] = await Promise.all([
        client.onboarding.getState(),
        client.setup.listJobs()
      ])
      if (!this.current(generation)) return
      if (onboarding.stage === 'returning') onboarding = await client.onboarding.returnToWelcome()
      if (!this.current(generation)) return
      if (onboarding.stage === 'committing') {
        this.update({
          onboarding,
          view: 'error',
          problem: '初始化提交未完成，请重试以继续准备正式数据。'
        })
        return
      }
      let initialView = 'resources'
      if (onboarding.initialized) initialView = 'home'
      else if (onboarding.stage === 'welcome' || onboarding.stage === 'preparing')
        initialView = 'welcome'
      this.update({
        onboarding,
        view: initialView
      })
      const downloads = jobs.filter(
        (job) =>
          job.kind === 'download' &&
          (job.preparationId ?? null) === (onboarding.preparationId ?? null) &&
          job.status !== 'cancelled' &&
          (job.status !== 'planned' || job.expiresAt > Date.now())
      )
      const job =
        downloads.find((item) => item.status === 'running') ??
        downloads.find((item) => item.status !== 'succeeded') ??
        downloads[0]
      if (onboarding.stage === 'importing') {
        const imported = jobs.find((item) => item.id === onboarding.jobId)
        if (!imported) throw new Error('未找到旧版导入任务，请重试。')
        this.watchJob(imported)
      } else if (job && onboarding.stage !== 'welcome') this.watchJob(job)
      let wasConnected = false
      this.stopConnection = client.observeConnection(({ connected, problem }) => {
        if (!this.current(generation)) return
        this.update({ connected, connectionProblem: problem?.message ?? null })
        if (connected && wasConnected) {
          void this.refreshFeatures()
          if (this.state.managedCatalog) void this.refreshManagedCatalog({ localChanged: true })
        }
        wasConnected ||= connected
      })
      this.stopOnboarding = client.observe('onboarding', null, (snapshot) => {
        if (!this.current(generation)) return
        this.update({ onboarding: snapshot })
      })
      if (onboarding.initialized) this.update({ view: 'home' })
      else if (onboarding.stage === 'preparing') await this.quickStart()
      else if (this.state.view === 'import') await this.advanceLegacyIfComplete()
      else if (this.state.view !== 'welcome') await this.loadResources()
    } catch (error) {
      if (this.current(generation)) this.update({ view: 'error', problem: error.message })
    }
  }

  /** @returns {Promise<void>} 记录可撤回的快速开始选择，不创建正式角色或连接。 */
  quickStart = async () => {
    if (this.state.pending || !this.client) return
    const generation = this.generation
    this.update({ pending: 'quick', problem: null })
    try {
      const onboarding = await this.client.onboarding.begin({ path: 'quick' })
      if (!this.current(generation)) return
      this.update({ onboarding, view: this.state.job ? 'download' : 'resources' })
      await this.loadResources()
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  async loadResources() {
    await Promise.all([this.loadCatalog(), this.refreshFeatures()])
    // 先展示内置目录；网络不可达仍能看到已有资源和明确的不可用原因。
    if (this.active && !this.remoteChecked) {
      this.remoteChecked = true
      void this.loadCatalog(true)
    }
  }
  async loadCatalog(refresh = false) {
    const generation = this.generation
    const revision = ++this.catalogRevision
    const selection = [...this.state.selection]
    this.update({ catalogLoading: true, catalogProblem: null, refreshing: refresh })
    try {
      const catalog = await this.client.setup.getCatalog({ selection, refresh })
      if (this.current(generation) && revision === this.catalogRevision) this.update({ catalog })
    } catch (error) {
      if (this.current(generation) && revision === this.catalogRevision)
        this.update({ catalogProblem: error.message || '无法读取资源目录，请重试。' })
    } finally {
      if (this.current(generation) && revision === this.catalogRevision)
        this.update({ catalogLoading: false, refreshing: false })
    }
  }
  refreshCatalog = () => {
    if (this.state.refreshing || this.state.pending) return Promise.resolve()
    return this.loadCatalog(true)
  }
  async refreshFeatures() {
    const generation = this.generation
    const revision = ++this.featureRevision
    try {
      const features = await this.client.setup.getFeatureState()
      if (this.current(generation) && revision === this.featureRevision) this.update({ features })
    } catch {
      // 就绪查询失败保持未知，不把组件文件下载完成推断成语音可用。
      if (this.current(generation) && revision === this.featureRevision)
        this.update({ features: null })
    }
  }

  /** @param {{force?: boolean, localChanged?: boolean}} [options] 手动检查远端或刷新已发生的本地变更。 @returns {Promise<void>} 保留最后一次成功快照，避免重进设置时出现空白。 */
  refreshManagedCatalog = ({ force = false, localChanged = false } = {}) => {
    if (!this.active || !this.client) return Promise.resolve()
    if (this.managedRequest && !localChanged) return this.managedRequest
    const readLocal = localChanged || !this.state.managedCatalog
    const checkOnline =
      force ||
      (!localChanged &&
        (this.managedCheckedAt === null ||
          Date.now() - this.managedCheckedAt >= managedRefreshIntervalMs))
    if (!readLocal && !checkOnline) return Promise.resolve()
    const generation = this.generation
    const revision = ++this.managedRevision
    const isCurrent = () => this.current(generation) && this.managedRevision === revision
    this.update({ managedCatalogChecking: true, managedCatalogProblem: null })
    const load = async () => {
      try {
        if (readLocal) {
          const value = await this.client.setup.getCatalog({ managed: true })
          if (isCurrent()) this.update({ managedCatalog: value })
        }
        if (isCurrent() && checkOnline) {
          this.managedCheckedAt = Date.now()
          const value = await this.client.setup.getCatalog({ managed: true, refresh: true })
          if (isCurrent()) this.update({ managedCatalog: value })
        }
      } catch (error) {
        if (isCurrent()) this.update({ managedCatalogProblem: error.message })
      } finally {
        if (isCurrent()) {
          this.managedRequest = null
          this.update({ managedCatalogChecking: false })
        }
      }
    }
    this.managedRequest = Promise.resolve().then(load)
    return this.managedRequest
  }

  /** @param {string} id 产品选项。 @returns {void} 勾选角色时自动选上所需引擎；取消引擎同时清除依赖它的语音选择。 */
  toggle = (id) => {
    if (this.state.pending || this.state.refreshing || this.state.view !== 'resources') return
    const options = this.state.catalog?.groups.flatMap((group) => group.options) ?? []
    const option = options.find((item) => item.id === id)
    if (!option?.available || option.ready) return
    const selection = new Set(this.state.selection)
    if (selection.has(id)) {
      selection.delete(id)
      for (const item of options) if (item.requiresSelection === id) selection.delete(item.id)
    } else {
      if (option.requiresSelection) selection.add(option.requiresSelection)
      selection.add(id)
    }
    this.planRequest = null
    this.update({ selection: [...selection], problem: null })
    void this.loadCatalog()
  }

  watchJob(job) {
    this.stopJob?.()
    let view = 'download'
    if (job.kind === 'legacy') view = 'import'
    if (this.state.onboarding?.initialized) view = 'home'
    this.update({ job, view })
    const generation = this.generation
    this.stopJob = this.client.observe('setup', job.id, (snapshot) => {
      if (!this.current(generation) || this.state.job?.id !== snapshot.id) return
      this.jobRevision += 1
      const changed =
        this.state.job.status !== snapshot.status ||
        this.state.job.download?.components.filter((item) => item.status === 'ready').length !==
          snapshot.download?.components.filter((item) => item.status === 'ready').length
      this.update({ job: snapshot })
      if (snapshot.kind === 'legacy') void this.advanceLegacyIfComplete()
      else if (changed) {
        void this.refreshFeatures()
        if (
          this.state.managedCatalog &&
          ['succeeded', 'failed', 'cancelled'].includes(snapshot.status)
        )
          void this.refreshManagedCatalog({ localChanged: true })
      }
    })
  }

  /** @returns {Promise<void>} 先生成持久计划再受理；丢失计划响应时复用命令身份，避免创建两份任务。 */
  download = async () => {
    if (
      this.state.pending ||
      this.state.catalogLoading ||
      this.state.catalogProblem ||
      !this.state.selection.length
    )
      return
    const options = this.state.catalog?.groups.flatMap((group) => group.options) ?? []
    if (this.state.selection.some((id) => !options.find((item) => item.id === id)?.available))
      return
    const generation = this.generation
    this.update({ pending: 'download', problem: null })
    try {
      // 先恢复此前已受理的任务，不用本次选择覆盖其持久状态。
      const jobs = await this.client.setup.listJobs()
      if (!this.current(generation)) return
      const existing = jobs.find(
        (job) =>
          (job.preparationId ?? null) === (this.state.onboarding?.preparationId ?? null) &&
          (job.status === 'running' ||
            (job.kind === 'download' && ['paused', 'failed'].includes(job.status)))
      )
      if (existing) {
        if (existing.kind !== 'download') throw new Error('已有资源准备任务正在执行。')
        this.watchJob(existing)
        return
      }
      this.planRequest ??= { requestId: this.randomId(), selection: [...this.state.selection] }
      const job = await this.client.call(
        'setup',
        'createPlan',
        [
          {
            kind: 'download',
            selection: this.planRequest.selection,
            preparationId: this.state.onboarding?.preparationId ?? null
          }
        ],
        { requestId: this.planRequest.requestId }
      )
      if (!this.current(generation)) return
      this.planRequest = null
      this.watchJob(job)
      await this.sendJobCommand('start', [job.id])
    } catch (error) {
      if (error.code) this.planRequest = null
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  /**
   * @param {string} selection 设置页选中的资源。
   * @returns {Promise<void>} 安装任务在页面关闭后仍继续。
   */
  async downloadManaged(selection, voiceUpdate) {
    if (this.state.pending) throw new Error('资源任务正在处理，请稍后再试。')
    const generation = this.generation
    this.update({ pending: 'download', problem: null })
    try {
      const jobs = await this.client.setup.listJobs()
      if (!this.current(generation)) return
      const existing = jobs.find((job) => job.status === 'running')
      if (existing && voiceUpdate) throw new Error('已有下载正在进行，请稍后再应用语音包。')
      if (existing && existing.kind !== 'download') throw new Error('已有资源准备任务正在执行。')
      const job =
        existing ??
        (await this.client.setup.createPlan({
          kind: 'download',
          managed: true,
          selection: [selection],
          voiceUpdate
        }))
      if (!this.current(generation)) return
      this.watchJob(job)
      if (!existing) await this.sendJobCommand('start', [job.id])
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  async sendJobCommand(method, args) {
    const generation = this.generation
    const revision = this.jobRevision
    const job = await this.client.call('setup', method, args)
    // WS 可能先于命令响应交付完成态，迟到的 running 响应不能覆盖它。
    if (this.current(generation) && revision === this.jobRevision) this.update({ job })
  }

  /**
   * @param {string} id 下载中心所选资源任务。
   * @param {'pause'|'resume'|'cancel'} action 下载操作。
   * @returns {Promise<void>} 继续其它任务时同步恢复就绪状态的订阅。
   */
  async controlDownload(id, action) {
    if (this.state.pending) throw new Error('资源任务正在处理，请稍后再试。')
    const generation = this.generation
    if (this.state.job?.id === id) {
      await this.sendJobCommand('controlJob', [id, action])
    } else {
      const job = await this.client.setup.controlJob(id, action)
      if (action === 'resume' && this.current(generation)) this.watchJob(job)
    }
  }

  /** @param {'pause'|'resume'|'cancel'} action 显式暂停或继续，同一身份支持失败重试。 @returns {Promise<void>} 等待后端安全节点。 */
  control = async (action) => {
    if (this.state.pending || !this.state.job) return
    const generation = this.generation
    this.update({ pending: action, problem: null })
    try {
      await this.sendJobCommand('controlJob', [this.state.job.id, action])
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
    await this.advanceLegacyIfComplete()
  }
  back = () => {
    if (this.state.pending) return
    if (this.state.view === 'legacy' && !this.state.onboarding?.path) {
      this.scanRevision += 1
      this.update({ view: 'welcome', scanning: false, picking: false, problem: null })
    } else if (this.state.view === 'resources' && this.state.onboarding?.path !== 'legacy')
      void this.returnQuick()
    else if (this.state.job?.status === 'succeeded' || this.state.job?.status === 'cancelled') {
      this.stopJob?.()
      this.stopJob = null
      this.update({ view: 'resources', job: null, selection: [], problem: null })
      void this.loadResources()
    }
  }
  /** @returns {Promise<void>} 后端确认撤回后才释放页面绑定，失败时保留原任务供重试。 */
  returnQuick = async () => {
    if (this.state.pending || this.state.onboarding?.path !== 'quick') return
    const generation = this.generation
    this.update({ pending: 'return', problem: null })
    try {
      const onboarding = await this.client.onboarding.returnToWelcome()
      if (!this.current(generation)) return
      this.stopJob?.()
      this.stopJob = null
      this.jobRevision += 1
      this.catalogRevision += 1
      this.featureRevision += 1
      this.planRequest = null
      this.update({
        onboarding,
        view: 'welcome',
        job: null,
        selection: [],
        catalog: null,
        features: null,
        catalogLoading: false,
        refreshing: false,
        catalogProblem: null
      })
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  /** @returns {Promise<void>} 用户重新选择时先确认旧任务取消，再释放页面绑定；失败保留原任务供重试。 */
  reselect = async () => {
    if (this.state.pending || !this.state.job || this.state.job.status === 'running') return
    const generation = this.generation
    this.update({ pending: 'reselect', problem: null })
    try {
      await this.client.setup.controlJob(this.state.job.id, 'cancel')
      if (!this.current(generation)) return
      this.stopJob?.()
      this.stopJob = null
      this.update({ view: 'resources', job: null, selection: [] })
      await this.loadResources()
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }
  /** @returns {void} 进入目录选择，不提前绑定初始化路径。 */
  openLegacy = () => {
    if (this.state.pending || this.state.onboarding?.path) return
    this.update({ view: 'legacy', problem: null })
  }

  /** @returns {Promise<void>} 取消系统选择器保留现有扫描；新目录使旧请求失效。 */
  chooseLegacy = async () => {
    if (this.state.pending || this.state.picking || this.state.view !== 'legacy') return
    const generation = this.generation
    const revision = this.scanRevision
    const pickerRevision = ++this.pickerRevision
    this.update({ picking: true })
    try {
      const source = await this.selectLegacyDirectory()
      if (
        !this.current(generation) ||
        revision !== this.scanRevision ||
        this.state.view !== 'legacy'
      )
        return
      if (source) await this.scanLegacy(source)
    } catch (error) {
      if (this.current(generation) && revision === this.scanRevision)
        this.update({ problem: error.message })
    } finally {
      if (this.current(generation) && pickerRevision === this.pickerRevision)
        this.update({ picking: false })
    }
  }

  /** @param {{sourceId: string, name: string}} source 授权目录。 @returns {Promise<void>} 扫描成功后默认选择有内容的类别。 */
  scanLegacy = async (source = this.state.legacySource) => {
    if (!source || this.state.pending) return
    const revision = ++this.scanRevision
    const generation = this.generation
    this.legacyPlan = null
    this.update({
      legacySource: source,
      legacyScan: null,
      legacyInclude: {},
      scanning: true,
      picking: false,
      problem: null
    })
    try {
      const scan = await this.client.setup.scanLegacy(source.sourceId)
      if (!this.current(generation) || revision !== this.scanRevision) return
      this.update({ legacyScan: scan, legacyInclude: { ...scan.availableCategories } })
    } catch (error) {
      if (this.current(generation) && revision === this.scanRevision)
        this.update({ problem: error.message })
    } finally {
      if (this.current(generation) && revision === this.scanRevision)
        this.update({ scanning: false })
    }
  }

  /** @param {string} key 整类选择。 @returns {void} 选择变化后重新生成计划。 */
  toggleLegacy = (key) => {
    if (
      this.state.pending ||
      this.state.scanning ||
      !this.state.legacyScan?.availableCategories[key]
    )
      return
    this.legacyPlan = null
    this.update({
      legacyInclude: { ...this.state.legacyInclude, [key]: !this.state.legacyInclude[key] },
      problem: null
    })
  }

  /** @returns {Promise<void>} 创建计划和开始导入分别可重试，丢失响应也沿用同一身份。 */
  importLegacy = async () => {
    if (
      this.state.pending ||
      this.state.scanning ||
      this.state.picking ||
      !this.state.legacyScan?.canImport ||
      !Object.values(this.state.legacyInclude).some(Boolean)
    )
      return
    const generation = this.generation
    this.update({ pending: 'import', problem: null })
    try {
      this.legacyPlan ??= { requestId: this.randomId(), job: null }
      if (!this.legacyPlan.job)
        this.legacyPlan.job = await this.client.call(
          'setup',
          'createPlan',
          [
            {
              kind: 'legacy',
              scanId: this.state.legacyScan.scanId,
              include: this.state.legacyInclude
            }
          ],
          { requestId: this.legacyPlan.requestId }
        )
      if (!this.current(generation)) return
      const job = this.legacyPlan.job
      const onboarding = await this.client.onboarding.begin({ path: 'legacy', planId: job.id })
      if (!this.current(generation)) return
      this.update({ onboarding })
      const snapshot = await this.client.setup.getJob(job.id)
      if (this.current(generation)) this.watchJob(snapshot)
    } catch (error) {
      if (this.current(generation)) {
        this.update({ problem: error.message })
        // begin 可能已经持久绑定任务；网络响应丢失不能让界面误以为仍可换来源。
        try {
          const onboarding = await this.client.onboarding.getState()
          if (onboarding.stage === 'importing' && onboarding.jobId) {
            const snapshot = await this.client.setup.getJob(onboarding.jobId)
            if (this.current(generation)) {
              this.update({ onboarding })
              this.watchJob(snapshot)
            }
          } else if (error.code) this.legacyPlan = null
        } catch {
          /* 保留计划身份，重试沿用同一任务。 */
        }
      }
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
    await this.advanceLegacyIfComplete()
  }

  /** @returns {Promise<void>} 仅无告警、无部分失败的完整成功自动进入附加资源。 */
  advanceLegacyIfComplete = async () => {
    const job = this.state.job
    if (
      this.state.view !== 'import' ||
      job?.status !== 'succeeded' ||
      job.warnings?.length ||
      Object.values(job.items ?? {}).some((item) => ['partial', 'failed'].includes(item.status))
    )
      return
    if (!this.state.pending) await this.continueLegacy()
  }

  /** @returns {Promise<void>} 保留真实已提交结果；零提交时由后端补齐快速开始。 */
  continueLegacy = async () => {
    if (
      this.state.pending ||
      this.state.job?.kind !== 'legacy' ||
      this.state.job.status === 'running'
    )
      return
    const generation = this.generation
    this.update({ pending: 'continue', problem: null })
    try {
      const onboarding =
        this.state.job.status === 'succeeded'
          ? await this.client.onboarding.prepareOptionalResources()
          : await this.client.onboarding.skipLegacyImport()
      if (!this.current(generation)) return
      this.stopJob?.()
      this.stopJob = null
      this.jobRevision += 1
      this.update({ onboarding, job: null, view: 'resources', selection: [] })
      await this.loadResources()
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  /** @returns {Promise<void>} 停止旧任务后重新扫描，已提交内容不回滚。 */
  reselectLegacy = async () => {
    if (this.state.pending || this.state.job?.status === 'running') return
    await this.control('cancel')
    if (!this.active || this.state.job?.status !== 'cancelled') return
    this.stopJob?.()
    this.stopJob = null
    this.jobRevision += 1
    this.legacyPlan = null
    this.update({ view: 'legacy', job: null, legacyScan: null, legacyInclude: {}, problem: null })
    if (this.state.legacySource) await this.scanLegacy()
  }

  /** @returns {Promise<void>} 重新选择期间仍可回看已保留的导入结果。 */
  returnToLegacyResult = async () => {
    if (this.state.pending || !this.state.onboarding?.jobId) return
    const generation = this.generation
    this.scanRevision += 1
    this.update({ pending: 'result', scanning: false, problem: null })
    try {
      const job = await this.client.setup.getJob(this.state.onboarding.jobId)
      if (this.current(generation)) this.watchJob(job)
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  /** @returns {Promise<void>} 最终提交后进入主页，仅恢复本次点击前正在运行的下载。 */
  finish = async () => {
    if (this.state.pending) return
    const generation = this.generation
    if (this.state.job?.kind === 'download' && this.state.job.status === 'running')
      this.finishResumeId = this.state.job.id
    const resumeId = this.finishResumeId
    this.update({ pending: 'finish', problem: null })
    try {
      const onboarding = await this.client.onboarding.finish()
      if (!this.current(generation)) return
      this.update({ onboarding, view: 'home' })
      if (resumeId) {
        const job = await this.client.setup.getJob(resumeId)
        if (job.status === 'paused') await this.client.setup.start(resumeId)
      }
      this.finishResumeId = null
    } catch (error) {
      if (this.current(generation)) this.update({ problem: error.message })
    } finally {
      if (this.current(generation)) this.update({ pending: null })
    }
  }

  retry = async () => {
    const generation = this.generation
    if (this.state.onboarding?.stage === 'committing' && this.client) {
      try {
        await this.client.onboarding.finish()
        if (!this.current(generation)) return
      } catch (error) {
        this.update({ problem: error.message })
        return
      }
    }
    this.dispose()
    return this.mount()
  }
  /** @returns {void} 释放订阅与连接；不暂停或取消业务任务，也不标记引导完成。 */
  dispose() {
    this.active = false
    this.generation += 1
    this.scanRevision += 1
    this.stopJob?.()
    this.stopOnboarding?.()
    this.stopConnection?.()
    this.client?.close()
    this.client = null
    this.remoteChecked = false
    this.managedRevision += 1
    this.managedRequest = null
    this.managedCheckedAt = null
    this.update({
      managedCatalog: null,
      managedCatalogChecking: false,
      managedCatalogProblem: null
    })
  }
}
