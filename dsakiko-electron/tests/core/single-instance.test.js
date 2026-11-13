import { EventEmitter } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { expect, test, vi } from 'vitest'
import { installSingleInstance } from '../../src/main/single-instance.js'
import { installWindowCloseBehavior } from '../../src/main/windows/close-behavior.js'

function fixture(primary) {
  const app = new EventEmitter()
  app.requestSingleInstanceLock = vi.fn(() => primary)
  app.quit = vi.fn()
  const window = {
    isDestroyed: () => false,
    isMinimized: () => true,
    restore: vi.fn(),
    show: vi.fn(),
    focus: vi.fn()
  }
  const instance = installSingleInstance(app, () => window)
  return { app, window, instance }
}

test('重复启动立即退出第二实例，不需要创建后端或窗口', () => {
  const { app, window, instance } = fixture(false)
  expect(instance.primary).toBe(false)
  expect(app.quit).toHaveBeenCalledOnce()
  expect(window.show).not.toHaveBeenCalled()
  expect(app.listenerCount('second-instance')).toBe(0)
})

test('第一个窗口就绪后处理提前到达的唤醒，最小化时恢复并聚焦', () => {
  const { app, window, instance } = fixture(true)
  app.emit('second-instance')
  expect(window.show).not.toHaveBeenCalled()
  instance.windowReady()
  expect(window.restore).toHaveBeenCalledOnce()
  expect(window.show).toHaveBeenCalledOnce()
  expect(window.focus).toHaveBeenCalledOnce()
  app.emit('activate')
  expect(window.focus).toHaveBeenCalledTimes(2)
  expect(app.quit).not.toHaveBeenCalled()
})

test('macOS 关窗隐藏，Windows 首次选择持久化并允许改为退出', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'dsakiko-window-'))
  try {
    const app = { name: '数字小祥', quit: vi.fn() }
    const dialog = { showMessageBox: vi.fn(async () => ({ response: 0 })) }
    const window = new EventEmitter()
    Object.assign(window, {
      isDestroyed: () => false,
      hide: vi.fn()
    })
    const file = join(directory, 'preferences.json')
    const event = { preventDefault: vi.fn() }
    let quitting = false
    const common = { app, window, dialog, file, isQuitting: () => quitting }
    installWindowCloseBehavior({ ...common, platform: 'darwin' })
    window.emit('close', event)
    expect(window.hide).toHaveBeenCalledOnce()
    expect(app.quit).not.toHaveBeenCalled()
    window.removeAllListeners()
    let restore
    Object.assign(window, { isMinimized: () => false, show: vi.fn(), focus: vi.fn() })
    class Tray {
      setToolTip() {}
      setContextMenu() {}
      on(event, handler) {
        restore = handler
      }
    }
    const Menu = { buildFromTemplate: (items) => items }
    const behavior = installWindowCloseBehavior({ ...common, platform: 'win32', Menu, Tray })
    window.emit('close', event)
    await expect.poll(() => window.hide.mock.calls.length).toBe(2)
    expect(await behavior.getBehavior()).toBe('tray')
    restore()
    expect(window.show).toHaveBeenCalledOnce()
    await behavior.setBehavior('quit')
    window.emit('close', event)
    await expect.poll(() => app.quit.mock.calls.length).toBe(1)
    expect(dialog.showMessageBox).toHaveBeenCalledOnce()
    quitting = true
    const finalClose = { preventDefault: vi.fn() }
    window.emit('close', finalClose)
    expect(finalClose.preventDefault).not.toHaveBeenCalled()
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})
