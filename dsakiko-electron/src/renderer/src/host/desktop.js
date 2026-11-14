/**
 * 获取受 preload 限制的桌面能力，业务通信另由 BackendClient 负责。
 * @returns {import('../../../shared/contracts/api.js').DesktopHost} 当前窗口的宿主接口。
 */
export function desktopHost() {
  if (!window.dsakikoHost) throw new Error('无法连接桌面宿主，请重新打开程序。')
  return window.dsakikoHost
}
