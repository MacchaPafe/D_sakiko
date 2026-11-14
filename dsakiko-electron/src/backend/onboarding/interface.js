import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { readJson, writeJson, requireValue, SerialQueue, SnapshotBus } from '../core/support.js'
import { installBuiltinConnection } from '../setup/bindings.js'
import { BuiltinResources } from './builtin-resources.js'

/**
 * @typedef {object} BuiltinForm
 * @property {string} id 稳定形态身份。
 * @property {string} directory 模型目录相对于内置包的位置。
 * @property {string} entry 模型目录内的入口文件。
 * @property {string} [displayName] 形态显示名，省略时沿用角色名。
 * @property {string} [description] 本形态人设，省略时沿用角色人设。
 * @property {string} [modelName] 模型展示名，省略时为“默认”。
 */
/**
 * @typedef {object} BuiltinCharacter
 * @property {string} id 稳定角色身份。
 * @property {string} name 角色名。
 * @property {string} description 兼容旧清单的共用人设。
 * @property {string | null} themeColor 角色主题色。
 * @property {string} [defaultFormId] 正式默认形态，旧清单省略时使用第一项。
 * @property {string} [previewFormId] 欢迎页只读预览形态，不写入正式角色选择。
 * @property {BuiltinForm[]} forms 内置形态与各自的模型。
 * @property {string} [archive] 相对于内置目录的角色 tar.gz 归档。
 * @property {number} [expandedBytes] 角色包内文件的总大小，限制解压空间。
 */
/** @typedef {{version: 1, contentVersion?: string, characters: BuiltinCharacter[]}} BuiltinManifest 内置资源清单；内容版本独立于格式版本，不包含文件哈希。 */

/** 初始化状态与可选资源任务相互独立，通知确认由 Notices 单独持有。 */
export class Onboarding {
  constructor({
    appDataRoot,
    cacheRoot,
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
    this.builtinResources = new BuiltinResources({ cacheRoot })
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
      completedAt: null
    })
  }
  /** @returns {Promise<import('../../shared/contracts/setup.js').OnboardingState>} 初始化进度与连接配置状态，不含连接凭据或通知确认副本。 */
  async getState() {
    const state = await this.state()
    const { generation } = await this.settings.get()
    const connection = (await this.settings.listConnections()).find(
      (item) => item.id === generation.connectionId
    )
    const requiresCredential =
      connection && !['openai-compatible', 'ollama', 'vllm'].includes(connection.provider)
    const configured =
      !!connection?.models.some((model) => model.id === generation.modelId) &&
      (!requiresCredential || connection.credentialConfigured)
    const connectionState = configured
      ? connection.id === 'builtin-up'
        ? 'builtin'
        : 'configured'
      : 'missing'
    // 未发布原型的确认字段不再参与公开契约或后续写入。
    delete state.noticeAcknowledged
    return { ...state, connectionState }
  }
  async save(state) {
    delete state.noticeAcknowledged
    await writeJson(this.file, state)
    this.bus.emit('state', await this.getState())
    await this.onNoticeChange?.()
  }

  /** @returns {Promise<BuiltinManifest>} 读取清单，不扫描模型内容或计算哈希。 */
  async readBuiltins() {
    const manifest = await readJson(path.join(this.builtinDirectory, 'manifest.json'))
    requireValue(
      manifest?.version === 1 && Array.isArray(manifest.characters),
      'builtin_resources_missing',
      '内置角色资源缺失，请重新安装主程序。'
    )
    return manifest
  }

  /**
   * 宿主在窗口创建后异步预热白祥；完成初始化的日常启动只清理遗留缓存。
   * @returns {Promise<{characterId: string, formId: string, directory: string, entry: string} | null>} 后端内部的预览来源，不能作为磁盘路径接口向页面开放。
   */
  async prepareStartupPreview() {
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'onboarding_closed')
      if ((await this.state()).initialized) {
        await this.builtinResources.clear()
        return null
      }
      return this.prepareBuiltinPreview()
    })
  }

  /**
   * 按需准备欢迎页角色，调用者可等待启动预热中的同一缓存；不影响正式默认形态。
   * @param {string} [characterId] 预览角色，默认祥子。
   * @returns {Promise<{characterId: string, formId: string, directory: string, entry: string}>} 本机内部预览来源，文件保留到后端关闭。
   */
  async prepareBuiltinPreview(characterId = 'sakiko') {
    requireValue(!this.closed, 'onboarding_closed')
    const manifest = await this.readBuiltins()
    const role = manifest.characters.find((item) => item.id === characterId)
    requireValue(role, 'builtin_character_missing')
    const form = role.forms.find((item) => item.id === (role.previewFormId ?? role.forms[0]?.id))
    requireValue(form, 'builtin_form_missing')
    return this.builtinResources.usingForm(
      this.builtinDirectory,
      manifest,
      role,
      form,
      async (directory) => ({
        characterId: role.id,
        formId: form.id,
        directory,
        entry: form.entry
      }),
      true
    )
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
      if (state.stage === 'optional-resources') return this.getState()
      state.path = options.path
      if (options.path === 'legacy') {
        const job = await this.setup.getJob(options.planId)
        requireValue(job.kind === 'legacy', 'invalid_onboarding_job')
        if (state.jobId && state.jobId !== job.id)
          requireValue(
            (await this.setup.getJob(state.jobId)).status !== 'running',
            'legacy_import_running'
          )
        state.jobId = job.id
        state.stage = 'importing'
        await this.save(state)
        await this.setup.start(job.id)
      } else await this.prepareQuick(state)
      return this.getState()
    })
  }

  async prepareQuick(state) {
    state.path = 'quick'
    state.stage = 'preparing'
    state.jobId = null
    await this.save(state)
    await this.ensureBuiltins()
    if (this.builtinConnection)
      await this.settings.update({
        generation: await installBuiltinConnection(this.settings, this.builtinConnection)
      })
    state.stage = 'optional-resources'
    await this.save(state)
  }

  /** 导入异常页确认保留已有内容并继续。 */
  async skipLegacyImport() {
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'onboarding_closed')
      const state = await this.state()
      if (state.initialized || state.stage === 'optional-resources') return this.getState()
      if (state.path === 'quick' && state.stage === 'preparing') {
        await this.prepareQuick(state)
      } else {
        requireValue(state.path === 'legacy' && state.jobId, 'invalid_onboarding_job')
        if (await this.setup.skipLegacy(state.jobId)) {
          await this.ensureBuiltins()
          state.stage = 'optional-resources'
          await this.save(state)
        } else {
          await this.prepareQuick(state)
        }
      }
      return this.getState()
    })
  }

  /** 释放缺失内置角色，已有身份或同名角色保持原样，不暴露手动补齐接口。 */
  async ensureBuiltins() {
    const manifest = await this.readBuiltins()
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
          const imported = await this.builtinResources.usingForm(
            this.builtinDirectory,
            manifest,
            role,
            form,
            (directory) =>
              this.assets.importAsset(
                { kind: 'local-directory', path: directory, entry: form.entry },
                owner,
                'live2d'
              )
          )
          forms.push({
            id: form.id,
            displayName: form.displayName ?? role.name,
            description: form.description ?? role.description,
            avatar: null,
            themeColor: role.themeColor,
            models: [
              {
                id: form.id + '-model',
                name: form.modelName ?? '默认',
                presentation: { model: imported.asset }
              }
            ],
            defaultModelId: form.id + '-model',
            voice: null
          })
        }
        await this.characters.save({
          id: role.id,
          name: role.name,
          revision: 0,
          // 预览形态只属于欢迎页；正式默认形态不随清单排序或预览选择改变。
          defaultFormId: role.defaultFormId ?? forms[0].id,
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
      if (state.initialized || state.stage === 'optional-resources') return this.getState()
      requireValue(state.path, 'onboarding_not_started')
      if (state.path === 'quick') {
        await this.prepareQuick(state)
        return this.getState()
      }
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
      state.completedAt ??= Date.now()
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
    await this.builtinResources.close((await this.state()).initialized)
  }
}
