/**
 * 演出材料的数据约定。角色身份、Chat、Turn、生成任务与取消原因不进入这些结构。
 * sequenceId 和 guideId 是一次演出模块生命周期内的不透明编号，不能持久化为消息身份。
 */

/** @typedef {import('./common.js').AssetRef} AssetRef */
/** @typedef {string} SequenceId */
/** @typedef {string} GuideId */
/** @typedef {'stage' | 'desktop-pet'} PresentationMode 普通演出或桌宠展示；作用于整个演出模块，不属于某条 sequence。 */

/**
 * 面向角色的逻辑演出选择，由演出模块解析到具体模型资源。
 * @typedef {object} PerformanceSelection
 * @property {string} motion 动作的逻辑名称，或 'auto'。
 * @property {string} expression 表情的逻辑名称，或 'auto'。
 */

/**
 * 一项模型能力的语义说明；可提供给 Agent 选择，不包含 SDK 对象。
 * @typedef {object} PerformanceOption
 * @property {string} id 稳定的逻辑名称。
 * @property {string} description 适合生成模块理解的中文说明。
 */

/**
 * @typedef {object} PerformanceCatalog
 * @property {PerformanceOption[]} motions 可选动作；允许为空。
 * @property {PerformanceOption[]} expressions 可选表情；允许为空。
 */

/**
 * @typedef {object} ModelPresentation
 * @property {AssetRef} model 模型资源入口，由演出模块内部选择 V2/V3 加载方式。
 * @property {AssetRef | null} mapping 逻辑动作、表情与模型资源的映射；null 使用模型默认映射。
 */

/**
 * @typedef {object} SceneSlot
 * @property {string} id 场景内的演员位置编号，guide 只引用这个编号。
 * @property {string} displayName 字幕显示名称。
 * @property {ModelPresentation | null} presentation 无模型时仍可播放语音和显示文本。
 * @property {{ x: number, y: number, scale: number, facing: 'left' | 'right' }} layout 归一化位置与缩放。
 */

/**
 * 场景内容与展示模式独立；切换模式不要求调用方重建场景或 guide。
 * @typedef {object} Scene
 * @property {AssetRef | null} background null 表示透明背景。
 * @property {SceneSlot[]} slots 演员位置；允许多个模型同屏，但同一 sequence 每次只推进一条 guide。
 */

/**
 * @typedef {object} AudioMaterial
 * @property {AssetRef} asset 可读取的音频资源。
 * @property {number} durationMs 音频实际时长，供后台定时推进使用。
 */

/**
 * @typedef {object} Guide
 * @property {string} slotId 目标演员位置，必须存在于 sequence 的场景中。
 * @property {string | null} text 演出文本；null 表示没有文本。
 * @property {string | null} translation 可选翻译，不自动作为语音合成文本。
 * @property {string | null} emotion 演出语义提示，不代表生成过程状态。
 * @property {PerformanceSelection} performance 动作和表情选择；均允许 'auto'。
 * @property {AudioMaterial | null} audio 没有语音或尚未提供语音时为 null。
 * @property {boolean} ready 是否允许开始；不携带尚未就绪的原因。
 */

/**
 * 为尚未就绪的 guide 提交最后一份材料，并原子地放行。
 * @typedef {object} GuideMaterials
 * @property {AudioMaterial | null} audio null 明确表示按无语音方式播放。
 */

/**
 * @typedef {object} GuideProgress 一个正在播放的 Guide 的播放进度。
 * @property {GuideId} guideId 当前 guide。
 * @property {string} slotId 当前演员位置。
 * @property {number} elapsedMs 当前这次播放已经经过的时间。
 * @property {number} estimatedDurationMs 估算总时长，可随实际动作和音频更新。
 * @property {number} revealedCharacters 已展示的文本字符数；由演出模块统一计算。
 */

/**
 * @typedef {object} SequenceSnapshot
 * @property {SequenceId} sequenceId 当前 sequence。
 * @property {number} revision 单调递增的观察版本，用于忽略迟到事件。
 * @property {boolean} foreground 是否被明确选择为前台。
 * @property {'empty' | 'waiting' | 'playing' | 'closed'} state 仅描述演出状态。
 * @property {GuideProgress | null} current 当前普通 guide；等待材料时为 null。
 * @property {GuideProgress | null} transition 当前过渡动作，不影响普通队首 waitingFor。
 * @property {GuideId | null} waitingFor 如果当前 sequence 正在因为某个 guide 而阻塞，则指向阻塞的 guide；如果当前没有阻塞，为 null。
 */

/**
 * guide 的终态；删除和关闭也会结束等待，但不伪装成正常播放完成。
 * @typedef {object} GuideFinish
 * @property {GuideId} guideId 对应 guide。
 * @property {'completed' | 'removed' | 'closed' | 'failed'} outcome 终态类别；播放故障有界结算，不携带业务取消原因。
 * @property {import('./common.js').Problem | null} problem 播放故障，即使降级完成也可报告；不是 TTS 故障。
 */

/**
 * @typedef {object} PerformanceUpdate
 * @property {SequenceSnapshot} snapshot 订阅时先发送当前快照，之后按版本发送更新。
 * @property {GuideFinish[]} finished 本次更新新进入终态的 guide；初始快照为空。
 * @property {import('./common.js').Problem | null} problem 本次音频或模型播放故障；不包含 TTS 状态。
 */

/**
 * 过渡只包含动作/表情，没有文本、翻译、音频或 ready；与普通 guide 共用稳定身份和终态等待。
 * @typedef {object} TransitionGuide
 * @property {string} slotId 必须存在的演员位置。
 * @property {PerformanceSelection} performance 动作与表情。
 * @property {boolean} recurring 队首可循环并阻塞后续过渡，循环一次不结算最终等待。
 */

/**
 * @typedef {object} BgmSettings
 * @property {AssetRef | null} audio 独立音轨资源，null 清空。
 * @property {boolean} enabled 全局开关。
 * @property {number} volume [0, 1]。
 * @property {boolean} loop 是否循环。
 */

/**
 * @typedef {object} BgmSnapshot
 * @property {BgmSettings} settings 当前音轨设置。
 * @property {'empty' | 'playing' | 'paused' | 'failed'} state 独立播放状态，不影响任何 Turn 完成。
 * @property {import('./common.js').Problem | null} problem 音轨故障，允许显式重试播放。
 */

export {}
