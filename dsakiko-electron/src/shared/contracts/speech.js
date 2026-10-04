/** @typedef {import('./common.js').AssetRef} AssetRef */
/** @typedef {string} SpeechTaskId Python 分配的不透明临时编号；结果过期或 Python 重启后失效，不持久化为消息身份。 */
/** @typedef {number} SpeechPriority 非负整数，越小越优先；0 为最高，1、2 是常用值，也允许其他整数。不抢占已开始的任务。 */

/**
 * 已解析且在一次任务中不变的发音配置，不接收完整角色对象。
 * @typedef {object} VoiceProfile
 * @property {AssetRef} gptWeights GPT 权重。
 * @property {AssetRef} sovitsWeights SoVITS 权重。
 * @property {AssetRef} referenceAudio 参考语音。
 * @property {string} referenceText 参考语音文本。
 * @property {string} referenceLanguage 参考语音语言。
 */

/**
 * @typedef {object} SpeechRequest
 * @property {string} text 实际需要发声的文本，不要求与界面显示文本相同。
 * @property {string} language 合成语言。
 * @property {VoiceProfile} voice 发音配置快照。
 * @property {SpeechPriority} priority 本次任务的初始优先级。
 * @property {number} [speed] 正的语速倍率；省略时使用 Python 合成配置的默认值。
 * @property {number} [sentencePauseMs] 非负整数，句间停顿毫秒数；省略时使用 Python 默认值。
 * @property {Array<{ text: string, reading: string }>} [pronunciationOverrides] 上游已解析的读音替换；客户端转换为实际发声文本，Python 不接收角色词典。
 */

/**
 * @typedef {{ status: 'succeeded', audio: import('./presentation.js').AudioMaterial }
 *   | { status: 'failed', problem: import('./common.js').Problem }
 *   | { status: 'cancelled' }} SpeechResult
 */

/**
 * 查询只暴露任务状态与终态材料，不暴露队列位置、模型实例或执行槽。
 * 成功材料已完成资源导入；通信或导入失败通过拒绝 Promise 报告，不伪装成 Python 的 failed 终态。
 * @typedef {{ taskId: SpeechTaskId } & ({ status: 'queued' | 'running' } | SpeechResult)} SpeechTaskSnapshot
 */

export {}
