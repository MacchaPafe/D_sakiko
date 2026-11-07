import { app, BrowserWindow } from 'electron'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 创建桌面主窗口；页面加载由应用入口负责。
 * @returns {BrowserWindow} 尚未加载页面的窗口。
 */
export function createMainWindow() {
  const resourceRoot = app.isPackaged ? process.resourcesPath : app.getAppPath()
  const window = new BrowserWindow({
    title: '数字小祥',
    // macOS 的应用图标由打包资源管理，避免覆盖后续的系统外观变体。
    icon: process.platform === 'darwin' ? undefined : join(resourceRoot, 'resources', 'icon.png'),
    width: 1360,
    height: 860,
    minWidth: 860,
    minHeight: 640,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#f5f7fb',
    webPreferences: {
      // 构建后窗口代码与入口合并到 out/main/index.js。
      preload: fileURLToPath(new URL('../preload/index.cjs', import.meta.url)),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
      autoplayPolicy: 'no-user-gesture-required'
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
