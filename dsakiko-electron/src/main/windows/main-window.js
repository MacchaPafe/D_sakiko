import { BrowserWindow } from 'electron'
import { fileURLToPath } from 'node:url'

/**
 * 创建桌面主窗口；页面加载由应用入口负责。
 * @returns {BrowserWindow} 尚未加载页面的窗口。
 */
export function createMainWindow() {
  const window = new BrowserWindow({
    title: '数字小祥',
    width: 1100,
    height: 760,
    minWidth: 640,
    minHeight: 480,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#f5f7fb',
    webPreferences: {
      // 构建后窗口代码与入口合并到 out/main/index.js。
      preload: fileURLToPath(new URL('../preload/index.cjs', import.meta.url)),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  window.once('ready-to-show', function showWindow() {
    window.show()
  })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', function preventNavigation(event) {
    event.preventDefault()
  })

  return window
}
