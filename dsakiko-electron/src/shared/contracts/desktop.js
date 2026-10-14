/** 仅供尚未替换的 React 原型使用；正式业务入口见 api.js。 */
/** 旧页面使用的命令名称；当前主进程已不注册这些业务 IPC。 */
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

export { problemError } from '../problems.js'

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
