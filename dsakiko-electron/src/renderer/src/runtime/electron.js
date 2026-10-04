/**
 * 将 preload 提供的能力适配为前端统一接口。
 * @param {import('../../../shared/contracts/runtime.js').RuntimeClient | undefined} bridge 桌面桥梁。
 * @returns {import('../../../shared/contracts/runtime.js').RuntimeClient} 异步运行时客户端。
 */
export function createElectronClient(bridge = window.dsakiko) {
  return {
    async getRuntimeInfo() {
      if (!bridge) throw new Error('桌面接口不可用，请使用 pnpm dev 启动 Electron。')
      return bridge.getRuntimeInfo()
    }
  }
}
