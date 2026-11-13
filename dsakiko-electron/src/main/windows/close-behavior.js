import { readJson, requireValue, SerialQueue, writeJson } from '../../backend/core/support.js'

/**
 * 单主窗口的关闭偏好独立于对话设置。显式退出由应用入口统一收尾。
 * @param {object} options 宿主、窗口和偏好存储。
 */
export function installWindowCloseBehavior({
  app,
  window,
  dialog,
  Menu,
  Tray,
  icon,
  file,
  platform,
  isQuitting
}) {
  const queue = new SerialQueue()
  let tray = null
  let pendingClose = false

  async function getBehavior() {
    requireValue(platform === 'win32', 'unsupported_platform')
    const { behavior } = (await readJson(file)) ?? {}
    return ['tray', 'quit'].includes(behavior) ? behavior : null
  }

  function saveBehavior(behavior) {
    requireValue(platform === 'win32', 'unsupported_platform')
    requireValue(['tray', 'quit'].includes(behavior), 'invalid_window_close_behavior')
    return writeJson(file, { behavior }).then(() => behavior)
  }

  function restore() {
    if (isQuitting() || window.isDestroyed()) return
    if (window.isMinimized()) window.restore()
    window.show()
    window.focus()
  }

  function hideToTray() {
    if (!tray) {
      const candidate = new Tray(icon)
      try {
        candidate.setToolTip(app.name)
        candidate.setContextMenu(
          Menu.buildFromTemplate([
            { label: '打开数字小祥', click: restore },
            { label: '退出程序', click: () => app.quit() }
          ])
        )
        candidate.on('click', restore)
        tray = candidate
      } catch (error) {
        candidate.destroy()
        throw error
      }
    }
    window.hide()
  }

  async function closeOnWindows() {
    let behavior = await getBehavior()
    if (!behavior) {
      const { response } = await dialog.showMessageBox(window, {
        type: 'question',
        message: '关闭窗口时怎么处理？',
        detail: '缩小到托盘后，导入和下载可以继续。你可以稍后在设置中更改此选择。',
        buttons: ['缩小到托盘', '退出程序', '取消'],
        defaultId: 0,
        cancelId: 2,
        noLink: true
      })
      if (response === 2 || isQuitting() || window.isDestroyed()) return
      behavior = await saveBehavior(response === 0 ? 'tray' : 'quit')
    }
    if (isQuitting() || window.isDestroyed()) return
    if (behavior === 'tray') hideToTray()
    else app.quit()
  }

  window.on('close', (event) => {
    if (isQuitting()) return
    event.preventDefault()
    if (platform === 'darwin') {
      window.hide()
    } else if (platform !== 'win32') {
      app.quit()
    } else if (!pendingClose) {
      pendingClose = true
      void queue
        .run('preference', closeOnWindows)
        .catch((error) => {
          console.error('关闭窗口失败', error)
          if (!isQuitting() && !window.isDestroyed())
            dialog.showErrorBox('无法关闭窗口', '请重试，或在设置中修改关闭窗口的行为。')
        })
        .finally(() => {
          pendingClose = false
        })
    }
  })

  function publishVisibility() {
    if (window.isDestroyed() || window.webContents.isDestroyed()) return
    const visible = window.isVisible() && !window.isMinimized()
    window.webContents.send('host:window-visibility', visible)
  }
  for (const event of ['show', 'hide', 'minimize', 'restore']) window.on(event, publishVisibility)
  window.on('closed', () => tray?.destroy())

  return {
    getBehavior,
    setBehavior: (behavior) => queue.run('preference', () => saveBehavior(behavior))
  }
}
