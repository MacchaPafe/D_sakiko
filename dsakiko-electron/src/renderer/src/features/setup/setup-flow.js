/**
 * @typedef {import('../../../../shared/contracts/setup.js').SetupSnapshot} SetupSnapshot
 * @typedef {{id: string, name: string, ready: boolean, available: boolean, sizeKnown: boolean, downloadBytes: number, requiresSelection?: string|null, unavailableReason: string|null}} ResourceOption
 * @typedef {{groups: Array<{id: string, options: ResourceOption[]}>, selectionDownloadBytes: number|null}} ResourceCatalog
 * @typedef {object} SetupFlowState
 * @property {'loading'|'welcome'|'resources'|'download'|'error'} view 当前页面；返回欢迎只改变展示，不回滚正式角色。
 * @property {import('../../../../shared/contracts/setup.js').OnboardingState|null} onboarding 后端初始化状态。
 * @property {ResourceCatalog|null} catalog 后端资源目录与去重估计。
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

/** 应用级初始化与下载会话；React 卸载只断开观察，不撤销后端任务。 */
export class SetupFlow {
  /** @param {{connect: () => Promise<import('../../backend/client.js').BackendClient>, randomId?: () => string}} options 传输连接及可验证的命令身份。 */
  constructor({ connect, randomId = () => crypto.randomUUID() }) {
    this.connect = connect
    this.randomId = randomId
    this.listeners = new Set()
    this.generation = 0
    this.catalogRevision = 0
    this.jobRevision = 0
    this.featureRevision = 0
    /** @type {SetupFlowState} */
    this.state = {
      view: 'loading',
      onboarding: null,
      catalog: null,
      selection: [],
      job: null,
      features: null,
      pending: null,
      catalogLoading: false,
      refreshing: false,
      connected: true,
      connectionProblem: null,
      problem: null,
      catalogProblem: null
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
      const [onboarding, jobs] = await Promise.all([
        client.onboarding.getState(),
        client.setup.listJobs()
      ])
      if (!this.current(generation)) return
      if (onboarding.stage === 'importing')
        throw new Error('当前有未完成的旧版导入，请先完成该导入流程。')
      this.update({
        onboarding,
        view:
          onboarding.stage === 'welcome' || onboarding.stage === 'preparing'
            ? 'welcome'
            : 'resources'
      })
      const downloads = jobs.filter(
        (job) =>
          job.kind === 'download' &&
          job.status !== 'cancelled' &&
          (job.status !== 'planned' || job.expiresAt > Date.now())
      )
      const job =
        downloads.find((item) => item.status === 'running') ??
        downloads.find((item) => item.status !== 'succeeded') ??
        downloads[0]
      if (job && onboarding.stage !== 'welcome') this.watchJob(job)
      let wasConnected = false
      this.stopConnection = client.observeConnection(({ connected, problem }) => {
        if (!this.current(generation)) return
        this.update({ connected, connectionProblem: problem?.message ?? null })
        if (connected && wasConnected) void this.refreshFeatures()
        wasConnected ||= connected
      })
      this.stopOnboarding = client.observe('onboarding', null, (snapshot) => {
        if (!this.current(generation)) return
        this.update({ onboarding: snapshot })
      })
      if (onboarding.stage === 'preparing') await this.quickStart()
      else if (this.state.view !== 'welcome') await this.loadResources()
    } catch (error) {
      if (this.current(generation)) this.update({ view: 'error', problem: error.message })
    }
  }

  /** @returns {Promise<void>} 幂等准备三位角色与默认连接；无论预览是否成功都可执行。 */
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

  /** @param {string} id 产品选项。 @returns {void} 勾选角色时自动选上所需引擎；取消引擎同时清除依赖它的语音选择。 */
  toggle = (id) => {
    if (
      this.state.pending ||
      this.state.refreshing ||
      this.state.view !== 'resources' ||
      id === 'asr'
    )
      return
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
    this.update({ job, view: 'download' })
    const generation = this.generation
    this.stopJob = this.client.observe('setup', job.id, (snapshot) => {
      if (!this.current(generation) || this.state.job?.id !== snapshot.id) return
      this.jobRevision += 1
      const changed =
        this.state.job.status !== snapshot.status ||
        this.state.job.download?.components.filter((item) => item.status === 'ready').length !==
          snapshot.download?.components.filter((item) => item.status === 'ready').length
      this.update({ job: snapshot })
      if (changed) void this.refreshFeatures()
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
          job.status === 'running' ||
          (job.kind === 'download' && ['paused', 'failed'].includes(job.status))
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
        [{ kind: 'download', selection: this.planRequest.selection }],
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

  async sendJobCommand(method, args) {
    const generation = this.generation
    const revision = this.jobRevision
    const job = await this.client.call('setup', method, args)
    // WS 可能先于命令响应交付完成态，迟到的 running 响应不能覆盖它。
    if (this.current(generation) && revision === this.jobRevision) this.update({ job })
  }

  /** @param {'pause'|'resume'} action 显式暂停或继续，同一身份支持失败重试。 @returns {Promise<void>} 等待后端安全节点。 */
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
  }
  back = () => {
    if (this.state.pending) return
    if (this.state.view === 'resources') this.update({ view: 'welcome', problem: null })
    else if (this.state.job?.status === 'succeeded' || this.state.job?.status === 'cancelled') {
      this.stopJob?.()
      this.stopJob = null
      this.update({ view: 'resources', job: null, selection: [], problem: null })
      void this.loadResources()
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
  retry = () => {
    this.dispose()
    return this.mount()
  }
  /** @returns {void} 释放订阅与连接；不暂停或取消业务任务，也不标记引导完成。 */
  dispose() {
    this.active = false
    this.generation += 1
    this.stopJob?.()
    this.stopOnboarding?.()
    this.stopConnection?.()
    this.client?.close()
    this.client = null
    this.remoteChecked = false
  }
}
