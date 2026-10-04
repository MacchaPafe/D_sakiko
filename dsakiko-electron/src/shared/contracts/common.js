/**
 * 核心接口共同使用的值与本地回调类型，不保存运行状态；函数不进入传输协议。
 * 所有时间长度使用毫秒；绝对时间使用 ISO 8601 字符串。
 * 返回的快照由调用方只读使用，不能通过修改快照改变模块状态。
 */

/** @typedef {null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }} JsonValue */
/** @typedef {Object<string, JsonValue>} Metadata 扩展数据；迁移时保留不认识的字段。 */
/** @typedef {() => void} Unsubscribe 取消本地订阅；重复调用无副作用。 */
/** @typedef {string} AssetOwnerId 资源持有者的不透明编号，可代表草稿、存档或任务；不是用户身份，也不授予访问权限。 */

/**
 * 稳定的资源引用，不是本机路径、临时 URL 或文件内容。
 * @typedef {object} AssetRef
 * @property {string} id 由资源模块分配；附件、语音与模型文件均通过引用访问。
 */

/**
 * 可向调用方报告的问题，不包含异常堆栈、凭据或内部对象。
 * 方法抛出或拒绝时使用带 problem 属性的 Error；传输层仅发送此数据，由本地代理重建错误。
 * @typedef {object} Problem
 * @property {string} code 稳定的问题类别，供调用方决定是否重试或降级。
 * @property {string} message 可展示的说明。
 * @property {boolean} retryable 是否允许重新提交同类请求。
 */

export {}
