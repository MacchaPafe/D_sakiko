/**
 * 全局默认设置。
 * @type {import('../../shared/contracts/settings.js').GlobalSettings}
 */
export const defaultSettings = {
  revision: 0,
  generation: {
    language: 'Japanese',
    translationLanguage: 'Chinese',
    temperature: 0.8,
    topP: 1,
    timeoutMs: 60000,
    maxOutputTokens: 4096,
    reasoning: { enabled: false, effort: null },
    compression: { enabled: true, thresholdTokens: 24000, retainedTurns: 2 },
    contextTokenBudget: 32000,
    limits: { maxSteps: 12, maxRequestRetries: 2, maxFormatRepairs: 2 }
  },
  speech: { enabled: false, speed: 1, sentencePauseMs: 300 },
  speechStartup: {
    device: 'cpu',
    precision: 'float32',
    architecture: 'v2',
    maxConcurrentInferences: 1,
    maxResidentModels: 1
  },
  bgm: { audio: null, enabled: false, loop: true },
  display: { frameRate: 60, theme: 'system', startInPetMode: false }
}

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
