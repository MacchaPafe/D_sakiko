import { PERFORMANCE_METHODS, toProblem } from '../../../shared/contracts/desktop.js'
import { Performance } from './performance.js'
import { StageSurface } from './surface.js'

/**
 * 应用入口创建唯一实例；React 页面只挂载舞台和观察状态。
 * @param {object} bridge preload 提供的受控能力。
 * @param {object} client 前端运行时客户端。
 * @returns {{performance: Performance, surface: StageSurface, dispose: function}} 应用级演出运行时。
 */
export function createPerformanceRuntime(bridge, client) {
  const surface = new StageSurface(client)
  const performance = new Performance({ surface })
  const stopCommands = bridge.onPerformanceCommand(async (command) => {
    if (!PERFORMANCE_METHODS.includes(command.method)) return
    try {
      const value = await performance[command.method](...command.args)
      bridge.replyPerformance({ id: command.id, value })
    } catch (error) {
      bridge.replyPerformance({ id: command.id, problem: toProblem(error) })
    }
  })
  const stopUpdates = performance.observeAll((update) => bridge.updatePerformance(update))
  return {
    performance,
    surface,
    dispose() {
      stopCommands()
      stopUpdates()
      performance.dispose()
      surface.dispose()
    }
  }
}
