/**
 * 创建能跨进程交付的问题异常。
 * @param {string} code 稳定错误类别。
 * @param {string} message 用户可读说明。
 * @param {boolean} [retryable] 是否可以重新尝试。
 * @returns {Error & {problem: import('./contracts/common.js').Problem}} 含问题数据的异常。
 */
export function problemError(code, message, retryable = false) {
  return Object.assign(new Error(message), { problem: { code, message, retryable } })
}
