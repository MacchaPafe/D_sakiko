import { ipcMain } from 'electron'
import { RUNTIME_INFO_CHANNEL } from '../../shared/contracts/runtime.js'

/**
 * 注册运行状态查询，并限制为主窗口的顶层页面调用。
 * @param {import('../../backend/create-backend.js').Backend} backend Node 后端实例。
 * @param {() => import('electron').BrowserWindow | null} getMainWindow 当前主窗口。
 * @returns {() => void} 移除处理器的清理函数。
 */
export function registerRuntimeIpc(backend, getMainWindow) {
  ipcMain.handle(RUNTIME_INFO_CHANNEL, function getRuntimeInfo(event) {
    const window = getMainWindow()
    if (
      !window ||
      window.isDestroyed() ||
      event.sender !== window.webContents ||
      event.senderFrame !== window.webContents.mainFrame
    ) {
      throw new Error('不允许此页面调用桌面接口。')
    }
    return backend.getRuntimeInfo()
  })

  return function unregisterRuntimeIpc() {
    ipcMain.removeHandler(RUNTIME_INFO_CHANNEL)
  }
}
