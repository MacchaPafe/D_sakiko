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
 * @property {PerformanceOption[]} motions 可供普通演出选择的动作；排除仅供状态切换的动作，允许为空。
 * @property {PerformanceOption[]} expressions 可选表情；允许为空。
 * @property {PerformanceOption[]} states 支持的布尔状态键及说明；不包含运行值，不作为 Agent 的台词动作候选。
 */

/**
 * @typedef {object} ModelStateAction
 * @property {string} motion 模型映射中存在的逻辑动作名，不能为 auto；须能从未知状态到达并保持指定目标，不能仅翻转当前值。
 */

/**
 * @typedef {object} ModelStateDefinition
 * @property {string} description 状态用途，例如“是否戴着面具”。
 * @property {boolean | null} [initial] 仅在新建模型实例且能确认声明初态时采用；省略或无法确认时为未知，原实例继续运行不重置。
 * @property {ModelStateAction} on 到达 true 的动作。
 * @property {ModelStateAction} off 到达 false 的动作；两个目标都必须有效，否则不发布该状态能力。
 */

/**
 * @typedef {object} ModelStateEffect
 * @property {string} state 同一模型已声明的状态键。
 * @property {boolean | null} value 动作成功结束后能确认的状态；null 表示会改变该状态但终值无法确认。
 */

/**
 * 模型 mapping 文件中的状态扩展；旧映射缺少这些字段时分别按空集合处理，不从动作名称猜状态能力。
 * states 的目标动作隐含对应状态效果，不再重复登记同一效果；涉及其他状态的影响仍由 stateEffects 声明。
 * 声明冲突、引用不存在或仅有无法确定目标的 toggle 动作时报告问题，不发布虚假的幂等能力。
 * @typedef {object} ModelStateMapping
 * @property {Object<string, ModelStateDefinition>} states 以稳定状态键组织的模型声明；第一版目标动作只支持 motion。
 * @property {{ motions: Object<string, ModelStateEffect[]>, expressions: Object<string, ModelStateEffect[]> }} stateEffects 其他动作/表情对状态的影响；含目标动作对其他状态的影响。
 */

/**
 * @typedef {object} ModelStateTarget
 * @property {string} slotId 场景内演员位置。
 * @property {string} state 当前模型支持的状态键。
 * @property {boolean} value 要达到的目标值。
 */

/**
 * 后端维护的模型计划状态，不保存进 Chat、角色配置或模型映射文件；不宣称任何客户端实例已经达到该值。
 * @typedef {object} ModelStateSnapshot
 * @property {string} slotId 演员位置。
 * @property {string} state 支持的状态键。
 * @property {boolean | null} value 计划动作完成后的目标；受影响动作开始或被抢占后为 null。实际模型状态留在各客户端。
 * @property {{ value: boolean, guideId: GuideId } | null} pending 最近一个尚未结算的目标请求；不表示已经达到目标。
 */

/**
 * @typedef {object} ModelPresentation
 * @property {AssetRef} model 模型资源入口，由演出模块内部选择 V2/V3 加载方式。
 * @property {AssetRef | null} mapping 逻辑动作、表情及 ModelStateMapping 状态扩展所在的映射文件；null 使用模型默认映射。
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
 * @property {number} [motionDurationMs] 可选的动作计划时长。
 * @property {boolean} ready 是否允许开始；不携带尚未就绪的原因。
 */

/**
 * 从已保存历史取得的回放材料；后端只读取材料，客户端独立播放，不创建后端 sequence 或改变 Turn 状态。
 * @typedef {object} ReplayMaterials
 * @property {Scene} scene 回放所需场景。
 * @property {Guide[]} guides 按历史顺序排列的台词，全部 ready；缺少已保存音频时 audio 为 null，不隐式合成。
 */

/**
 * 为尚未就绪的 guide 提交最后一份材料，并原子地放行。
 * @typedef {object} GuideMaterials
 * @property {AudioMaterial | null} audio null 明确表示按无语音方式播放。
 */

/**
 * @typedef {object} GuideProgress 一个正在推进的 Guide 的计划时间锚点。
 * @property {GuideId} guideId 当前 guide。
 * @property {string} slotId 当前演员位置。
 * @property {number} elapsedMs 当前这次播放已经经过的时间。
 * @property {Guide} guide 当前已固定的完整材料。
 * @property {number} startedAtMs 后端单调时钟上的开始时间。
 * @property {number} durationMs 固定计划时长，优先采用音频时长；无音频使用字数估算。
 * @property {boolean} recurring 是否为驻留过渡。
 */

/**
 * @typedef {object} SequenceSnapshot
 * @property {SequenceId} sequenceId 当前 sequence。
 * @property {number} revision 单调递增的观察版本，用于忽略迟到事件。
 * @property {string} runtimeId 后端运行身份；与 revision 一起识别重连和重启。
 * @property {number} serverTimeMs 生成快照时的后端单调时钟，不是客户端墙上时间。
 * @property {Scene} scene 当前场景。
 * @property {PerformanceSelection} defaultPresentation 空闲展示选择。
 * @property {'empty' | 'waiting' | 'playing' | 'closed'} state 仅描述演出状态。
 * @property {GuideProgress | null} current 当前普通 guide；等待材料时为 null。
 * @property {GuideProgress | null} transition 当前过渡动作，不影响普通队首 waitingFor。
 * @property {GuideId | null} waitingFor 如果当前 sequence 正在因为某个 guide 而阻塞，则指向阻塞的 guide；如果当前没有阻塞，为 null。
 * @property {ModelStateSnapshot[]} modelStates 当前各 slot 模型支持的状态及待执行目标；不支持的状态不伪造条目。
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
 * @property {number} [motionDurationMs] 动作元数据给出的计划时长，省略时使用后端约定值。
 * @property {boolean} recurring 队首可循环并阻塞后续过渡，循环一次不结算最终等待。
 */

/**
 * @typedef {object} BgmSettings
 * @property {AssetRef | null} audio 独立音轨资源，null 清空。
 * @property {boolean} enabled 全局开关。
 * @property {boolean} loop 是否循环。
 */

/**
 * @typedef {object} BgmSnapshot
 * @property {BgmSettings} settings 当前音轨设置。
 * @property {'empty' | 'playing' | 'paused' | 'failed'} state 独立播放状态，不影响任何 Turn 完成。
 * @property {import('./common.js').Problem | null} problem 音轨故障，允许显式重试播放。
 */

export {}
