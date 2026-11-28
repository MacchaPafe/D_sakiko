import { desktopHost } from './desktop.js'

let values = {}
let loading

/**
 * 在创建主页控制器前加载桌面偏好。读取失败时允许继续使用界面默认值。
 * @returns {Promise<void>} 首次加载或采用默认值后完成。
 */
export function loadUiPreferences() {
  loading ??= desktopHost()
    .getUiPreferences()
    .then((result) => {
      values = result
    })
    .catch((error) => console.error('读取界面偏好失败', error))
  return loading
}

/** 同步读取已加载的偏好，写入由宿主负责持久化。 */
export const uiPreferences = {
  getItem(key) {
    return Object.hasOwn(values, key) ? JSON.stringify(values[key]) : null
  },
  setItem(key, value) {
    if (!globalThis.window?.dsakikoHost) return
    const next = JSON.parse(value)
    values[key] = next
    void desktopHost()
      .setUiPreference(key, next)
      .catch((error) => {
        console.error('保存界面偏好失败', error)
      })
  }
}
