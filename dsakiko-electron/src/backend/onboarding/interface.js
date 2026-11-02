import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { readJson, writeJson, requireValue, SerialQueue, SnapshotBus } from '../core/support.js'
import { installBuiltinConnection } from '../setup/bindings.js'

/** 初始化状态与可选资源任务相互独立，跳过下载后仍可使用文字对话。 */
export class Onboarding {
  constructor({
    appDataRoot,
    setup,
    assets,
    characters,
    settings,
    builtinDirectory = fileURLToPath(
      new URL('../../../resources/builtin-characters/', import.meta.url)
    ),
    builtinConnection
  }) {
    Object.assign(this, {
      setup,
      assets,
      characters,
      settings,
      builtinDirectory,
      builtinConnection
    })
    this.file = path.join(appDataRoot, 'onboarding.json')
    this.queue = new SerialQueue()
    this.bus = new SnapshotBus()
    this.closed = false
  }
  async state() {
    return readJson(this.file, {
      version: 1,
      path: null,
      stage: 'welcome',
      jobId: null,
      initialized: false,
      noticeAcknowledged: false
    })
  }
  /** @returns {Promise<import('../../shared/contracts/setup.js').OnboardingState>} 初始化进度与主页提示，不含连接凭据。 */
  async getState() {
    const state = await this.state()
    const { generation } = await this.settings.get()
    const connection = (await this.settings.listConnections()).find(
      (item) => item.id === generation.connectionId
    )
    const configured = !!connection?.models.some((model) => model.id === generation.modelId)
    let notice = null
    if (!state.noticeAcknowledged) {
      if (!configured) notice = 'model-connection-missing'
      else if (state.path === 'quick') notice = 'configure-model-connection'
    }
    return { ...state, notice }
  }
  async save(state) {
    await writeJson(this.file, state)
    this.bus.emit('state', await this.getState())
  }

  /** 开始快速初始化，或绑定一个已经确认的旧版导入计划。
   * @param {{path: 'quick'|'legacy', planId?: string}} options 初始化路径和已确认计划。
   * @returns {Promise<import('../../shared/contracts/setup.js').OnboardingState>} 可以恢复的初始化状态。
   */
  async begin(options) {
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'onboarding_closed')
      const state = await this.state()
      if (state.initialized) return this.getState()
      requireValue(['quick', 'legacy'].includes(options?.path), 'invalid_onboarding_path')
      requireValue(!state.path || state.path === options.path, 'onboarding_already_started')
      state.path = options.path
      if (options.path === 'legacy') {
        const job = await this.setup.getJob(options.planId)
        requireValue(job.kind === 'legacy', 'invalid_onboarding_job')
        state.jobId = job.id
        state.stage = 'importing'
        await this.save(state)
        await this.setup.start(job.id)
      } else {
        state.stage = 'preparing'
        await this.save(state)
        await this.ensureBuiltins()
        if (this.builtinConnection)
          await this.settings.update({
            generation: await installBuiltinConnection(this.settings, this.builtinConnection)
          })
        state.stage = 'optional-resources'
        await this.save(state)
      }
      return this.getState()
    })
  }

  /** 释放缺失内置角色，已有身份或同名角色保持原样，不暴露手动补齐接口。 */
  async ensureBuiltins() {
    const manifest = await readJson(path.join(this.builtinDirectory, 'manifest.json'))
    requireValue(
      manifest?.version === 1,
      'builtin_resources_missing',
      '内置角色资源缺失，请重新安装主程序。'
    )
    for (const role of manifest.characters) {
      if (
        (await this.characters.list()).some(
          (item) => item.id === role.id || item.name === role.name
        )
      )
        continue
      await this.assets.using({}, async (owner) => {
        const forms = []
        for (const form of role.forms) {
          const imported = await this.assets.importAsset(
            {
              kind: 'local-directory',
              path: path.join(this.builtinDirectory, form.directory),
              entry: form.entry
            },
            owner,
            'live2d'
          )
          forms.push({
            id: form.id,
            displayName: role.name,
            description: role.description,
            avatar: null,
            themeColor: role.themeColor,
            models: [
              { id: form.id + '-model', name: '默认', presentation: { model: imported.asset } }
            ],
            defaultModelId: form.id + '-model',
            voice: null
          })
        }
        await this.characters.save({
          id: role.id,
          name: role.name,
          revision: 0,
          defaultFormId: forms[0].id,
          forms,
          knowledgeMappings: [],
          extensions: {}
        })
      })
    }
  }

  /** 导入完成后补齐角色并进入附加资源阶段；下载失败不改变初始化成功状态。 */
  async prepareOptionalResources() {
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'onboarding_closed')
      const state = await this.state()
      requireValue(state.path, 'onboarding_not_started')
      if (state.path === 'legacy')
        requireValue(
          (await this.setup.getJob(state.jobId)).status === 'succeeded',
          'legacy_import_incomplete',
          '请先完成旧版资料导入。'
        )
      await this.ensureBuiltins()
      state.stage = 'optional-resources'
      await this.save(state)
      return this.getState()
    })
  }

  /** 完成或跳过可选下载后进入主页，只记录初始化完成，不伪造语音就绪。 */
  async finish() {
    await this.prepareOptionalResources()
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'onboarding_closed')
      const state = await this.state()
      state.stage = 'complete'
      state.initialized = true
      await this.save(state)
      return this.getState()
    })
  }
  async acknowledgeNotice() {
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'onboarding_closed')
      const state = await this.state()
      state.noticeAcknowledged = true
      await this.save(state)
      return this.getState()
    })
  }
  async observe(listener) {
    return this.bus.subscribe('state', listener, await this.getState())
  }
  async close() {
    this.closed = true
    await this.queue.run('state', async () => this.bus.clear())
  }
}
