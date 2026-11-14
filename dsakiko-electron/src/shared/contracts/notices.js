/**
 * @typedef {object} ConnectionNoticeData
 * @property {'builtin'|'missing'} connectionState 当前连接配置状态，不表示已完成联网验证。
 */
/**
 * @typedef {object} VoiceNoticeData
 * @property {string} jobId 产生就绪结果的资源任务。
 * @property {string} characterId 稳定角色身份。
 * @property {string} characterName 当前显示名。
 * @property {string[]} readyFormIds 当前可用且属于此次就绪记录的形态。
 * @property {boolean} enabled 是否允许默认启用；对话级关闭仍优先。
 */
/**
 * @typedef {object} Notice
 * @property {string} id 稳定通知身份，重试与重启不改变。
 * @property {'connection'|'voice-ready'} kind 通知类型，由前端映射文案与样式。
 * @property {number} createdAt 来源产生时间，毫秒时间戳。
 * @property {number} order 首次登记的持久顺序；后补通知追加到末尾。
 * @property {ConnectionNoticeData|VoiceNoticeData} data 可公开的业务材料。
 */
/**
 * @typedef {object} NoticeSnapshot
 * @property {string} runtimeId 本次模块运行身份。
 * @property {number} revision 持久观察版本。
 * @property {Notice[]} notices 按产生顺序排列、仍有效且未关闭的通知。
 */
export {}
