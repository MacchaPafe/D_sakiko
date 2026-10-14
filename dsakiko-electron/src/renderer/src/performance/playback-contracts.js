/** 客户端播放偏好，只在前端存储和使用，不进入后端设置、Chat、Turn 或网络命令。 */

/**
 * @typedef {object} PlaybackOptions
 * @property {number} volume 本客户端总播放音量，有限值 [0, 1]；0 即静音，缺失、无效或清除本地值后为 1（100%）。
 * @property {number} bgmVolume 本客户端 BGM 相对音量，有限值 [0, 1]，缺失、无效或清除本地值后为 1；实际输出为 volume × bgmVolume。
 */

export {}
