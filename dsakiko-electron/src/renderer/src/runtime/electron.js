import { COMMANDS } from '../../../shared/contracts/desktop.js'

/**
 * 将固定 preload 命令转换为返回值或 Problem 异常。
 * @param {object} [bridge] Electron 桥。
 * @returns {object} 与传输细节无关的具名客户端。
 */
export function createElectronClient(bridge = window.dsakiko) {
  const client = {}
  for (const name of COMMANDS)
    client[name] = async (...args) => {
      if (!bridge) throw new Error('请通过 pnpm dev 启动 Electron。')
      const result = await bridge[name](...args)
      if (result.problem)
        throw Object.assign(new Error(result.problem.message), { problem: result.problem })
      return result.value
    }
  client.onUpdate = (listener) => bridge.onUpdate(listener)
  client.getRuntimeInfo = () => bridge.getRuntimeInfo()
  return client
}
