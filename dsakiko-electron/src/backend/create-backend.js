/**
 * @typedef {object} Backend
 * @property {() => import('../shared/contracts/runtime.js').RuntimeInfo} getRuntimeInfo 查询运行状态。
 */

/**
 * 创建与 Electron 无关的 Node 后端入口，业务模块在后续开发中接入。
 * @returns {Backend} 后端提供的能力。
 */
export function createBackend() {
  return {
    getRuntimeInfo() {
      return { status: 'ready' }
    }
  }
}
