/** 仅供旧 React 原型引用的类型；正式宿主能力见 api.js 的 DesktopHost。 */

/**
 * @typedef {object} RuntimeInfo
 * @property {'ready'} status 后端已完成初始化。
 */

/**
 * @typedef {object} RuntimeClient
 * @property {() => Promise<RuntimeInfo>} getRuntimeInfo 查询后端运行状态。
 */

export {}
