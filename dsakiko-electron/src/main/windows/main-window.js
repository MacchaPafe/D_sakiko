import { app, BrowserWindow, screen } from 'electron'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 创建桌面主窗口；页面加载由应用入口负责。
 * @returns {BrowserWindow} 尚未加载页面的窗口。
 */
export function createMainWindow() {
  const resourceRoot = app.isPackaged ? process.resourcesPath : app.getAppPath()
  const workArea = screen.getPrimaryDisplay().workAreaSize
  const window = new BrowserWindow({
    title: '数字小祥',
    // macOS 的应用图标由打包资源管理，避免覆盖后续的系统外观变体。
    icon: process.platform === 'darwin' ? undefined : join(resourceRoot, 'resources', 'icon.png'),
    width: Math.min(1360, workArea.width),
    height: Math.min(860, workArea.height),
    minWidth: Math.min(1024, workArea.width),
    minHeight: Math.min(680, workArea.height),
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#f4f1eb',
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
