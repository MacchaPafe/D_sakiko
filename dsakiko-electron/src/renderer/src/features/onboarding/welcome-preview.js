/**
 * @typedef {object} WelcomePreviewState
 * @property {'sakiko'|'anon'|'soyo'} characterId 当前选择，与正式角色配置无关。
 * @property {'loading'|'ready'|'error'} phase 当前预览状态。
 * @property {string|null} problem 可读的失败原因。
 */

/** 引导流程只读预览控制器；隔离异步选择、宿主连接和模型生命周期。 */
export class WelcomePreview {
  /**
   * @param {{connect: () => Promise<import('../../backend/client.js').BackendClient>, createSurface: (element: HTMLElement) => object, host: () => import('../../../../shared/contracts/api.js').DesktopHost}} options 窄能力注入，便于验证迟到结果与卸载。
   */
  constructor({ connect, createSurface, host }) {
    this.connect = connect
    this.createSurface = createSurface
    this.host = host
    this.listeners = new Set()
    this.generation = 0
    this.mounted = false
    /** @type {WelcomePreviewState} */
    this.state = { characterId: 'sakiko', phase: 'loading', problem: null }
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

  /** @param {HTMLElement} element 画布容器。 @returns {void} 装配一次窗口的预览。 */
  mount(element) {
    this.mounted = true
    try {
      this.surface = this.createSurface(element)
      const surface = this.surface
      const host = this.host()
      // 订阅后的事件优先于启动查询，避免迟到的可见状态重新启动隐藏窗口。
      let changed = false
      this.stopVisibility = host.onWindowVisibility((visible) => {
        changed = true
        surface.setVisible(visible)
      })
      void host
        .getWindowVisibility()
        .then((visible) => {
          if (!changed && this.surface === surface) surface.setVisible(visible)
        })
        .catch(() => {})
      this.connection = this.connect().then((client) => {
        if (!this.mounted || this.surface !== surface) {
          client.close()
          throw new Error('预览已关闭。')
        }
        this.client = client
        return client
      })
      void this.select(this.state.characterId)
    } catch (error) {
      this.update({ phase: 'error', problem: error.message })
    }
  }

  /** @param {number} width 当前页面的设计参考宽度。 @returns {void} 调整构图而不重新加载模型。 */
  setReferenceWidth(width) {
    this.surface?.setReferenceWidth(width)
  }

  /** @param {'sakiko'|'anon'|'soyo'} characterId 仅影响引导预览。 @returns {Promise<void>} 最新选择完成或进入可重试失败态。 */
  async select(characterId) {
    if (!this.mounted) return
    const generation = ++this.generation
    const surface = this.surface
    this.abort?.abort()
    const abort = new AbortController()
    this.abort = abort
    this.update({ characterId, phase: 'loading', problem: null })
    const current = () => this.mounted && generation === this.generation
    try {
      if (!surface || !this.connection) throw new Error('预览初始化失败，请重新打开程序。')
      surface.clear()
      const client = await this.connection
      if (!current()) return
      const preview = await client.call('onboarding', 'getBuiltinPreview', [characterId], {
        signal: abort.signal
      })
      if (!current()) return
      await surface.show(preview.url, current, abort.signal)
      if (current()) this.update({ phase: 'ready' })
    } catch (error) {
      if (current()) this.update({ phase: 'error', problem: error.message || '角色预览加载失败。' })
    }
  }

  /** @returns {void} 重试会重新获取短期预览地址；不复用过期 URL。 */
  retry = () => {
    if (!this.client || !this.surface) {
      window.location.reload()
      return
    }
    void this.select(this.state.characterId)
  }

  /** @returns {void} 释放监听、连接和图形资源，使迟到结果失效。 */
  dispose() {
    this.mounted = false
    this.generation += 1
    this.abort?.abort()
    this.stopVisibility?.()
    this.client?.close()
    this.client = null
    this.surface?.destroy()
    this.surface = null
  }
}
