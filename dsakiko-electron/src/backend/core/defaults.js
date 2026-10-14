/**
 * 后端只保存共享业务设置，音量与当前查看位置不在这里。
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
 * 新对话只保存覆盖值，生成时再与共享默认值解析。
 * @returns {import('../../shared/contracts/settings.js').ChatSettings} 每次创建独立设置，避免对话间共享可变对象。
 */
export function chatSettings() {
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
