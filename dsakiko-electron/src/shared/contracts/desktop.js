/** 桌面协议只列出本轮允许的入口；不暴露任意模块调用。 */
export const COMMANDS = [
  'getBootstrap',
  'getSettings',
  'saveSettings',
  'importLegacySettings',
  'listCharacters',
  'setCharacterModel',
  'listPersonas',
  'getPersona',
  'savePersona',
  'listChats',
  'createChat',
  'createTurn',
  'stopTurn',
  'selectConversation',
  'replayMessage',
  'deleteChat',
  'resolveAsset',
  'chooseResourceRoot',
  'choosePython',
  'performanceReady'
]
export const PERFORMANCE_METHODS = [
  'createSequence',
  'appendGuides',
  'resolveGuide',
  'removeGuides',
  'discardPendingGuides',
  'setForeground',
  'setScene',
  'getProgress',
  'closeSequence'
]
export const DESKTOP_EVENT = 'desktop:update'
export const PERFORMANCE_COMMAND = 'performance:command'
export const PERFORMANCE_REPLY = 'performance:reply'
export const PERFORMANCE_UPDATE = 'performance:update'

/**
 * 创建能跨进程交付的问题异常。
 * @param {string} code 稳定错误类别。
 * @param {string} message 用户可读说明。
 * @param {boolean} [retryable] 是否可以重新尝试。
 * @returns {Error & {problem: import('./common.js').Problem}} 含问题数据的异常。
 */
export function problemError(code, message, retryable = false) {
  return Object.assign(new Error(message), { problem: { code, message, retryable } })
}

/**
 * 将内部异常投影为有限的问题数据。
 * @param {unknown} error 内部异常。
 * @returns {import('./common.js').Problem} 可交付的问题。
 */
export function toProblem(error) {
  return (
    error?.problem || {
      code: 'operation_failed',
      message: '操作失败，请检查设置或运行日志。',
      retryable: true
    }
  )
}
