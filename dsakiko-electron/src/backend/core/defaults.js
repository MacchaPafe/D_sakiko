import { createDefaultSettings } from '../settings/definition.js'

/**
 * 全局默认设置。
 * @type {import('../../shared/contracts/settings.js').GlobalSettings}
 */
export const defaultSettings = createDefaultSettings()

/**
 * 创建一个空白的对话级设置对象。
 * @returns {import('../../shared/contracts/settings.js').ChatSettings} 新对话的初始设置对象。
 */
export function chatSettings() {
  // 为什么只保存空覆盖对象而不是直接复制全局默认值：
  // 仅记录用户针对该对话显式修改过的项，未修改的项在对话生成时再动态继承最新的全局设置。
  return {
    generation: {},
    speech: {},
    scenario: null,
    tools: { enabled: [] },
    worldbook: {
      enabled: false,
      rootPackageIds: [],
      progress: {},
      directContext: false,
      queryTools: false
    },
    characters: {},
    presentation: { background: null, characters: {} }
  }
}
