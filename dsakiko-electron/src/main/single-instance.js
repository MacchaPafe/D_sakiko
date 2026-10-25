/**
 * 在后端打开数据前取得桌面实例锁，重复启动只唤醒已有窗口。
 * @param {import('electron').App} app Electron 应用。
 * @param {() => import('electron').BrowserWindow | null} getWindow 当前主窗口。
 * @returns {{primary: boolean, windowReady: () => void}} 是否允许启动，以及窗口就绪通知。
 */
export function installSingleInstance(app, getWindow) {
  const primary = app.requestSingleInstanceLock()
  let requested = false
  let ready = false
  function wake() {
    requested = true
    const window = getWindow()
    if (!ready || !window || window.isDestroyed()) return
    if (window.isMinimized()) window.restore()
    window.show()
    window.focus()
    requested = false
  }
  if (primary) {
    app.on('second-instance', wake)
    app.on('activate', wake)
  } else app.quit()
  return {
    primary,
    windowReady() {
      ready = true
      if (requested) wake()
    }
  }
}
