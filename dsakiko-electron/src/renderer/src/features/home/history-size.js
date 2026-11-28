import { uiPreferences } from '../../host/ui-preferences.js'

export const HISTORY_SIZE_KEY = 'historySize'
/** @typedef {{width: number, height: number}} HistorySize 记录框偏好，尺寸单位为 CSS 像素。 */
/** @param {Pick<Storage, 'getItem'>|null} storage 本机偏好。 @returns {HistorySize} 无效偏好退回设计尺寸。 */
export function readHistorySize(storage = uiPreferences) {
  try {
    const size = JSON.parse(storage?.getItem(HISTORY_SIZE_KEY) ?? 'null')
    if (Number.isFinite(size?.width) && Number.isFinite(size?.height))
      return {
        width: Math.max(280, Math.min(1600, size.width)),
        height: Math.max(220, Math.min(1600, size.height))
      }
  } catch {
    /* 偏好损坏不影响历史阅读。 */
  }
  return { width: 360, height: 671 }
}
/** @param {HistorySize} preferred 用户偏好。 @param {HistorySize} available 可用空间。 @returns {HistorySize} 仅约束显示，不覆盖偏好。 */
export function fitHistorySize(preferred, available) {
  return {
    width: Math.min(preferred.width, Math.max(160, available.width)),
    height: Math.min(preferred.height, Math.max(160, available.height))
  }
}
