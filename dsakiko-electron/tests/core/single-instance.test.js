import { EventEmitter } from 'node:events'
import { expect, test, vi } from 'vitest'
import { installSingleInstance } from '../../src/main/single-instance.js'

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
