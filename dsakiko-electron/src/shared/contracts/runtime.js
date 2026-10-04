/**
 * @typedef {object} RuntimeInfo
 * @property {'ready'} status 后端已完成初始化。
 */

/**
 * @typedef {object} RuntimeClient
 * @property {() => Promise<RuntimeInfo>} getRuntimeInfo 查询后端运行状态。
 */

export const RUNTIME_INFO_CHANNEL = 'runtime:get-info'
