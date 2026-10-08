/**
 * 配置声明，不提供默认值实现。省略覆盖字段表示继承；数组整体替换，未知业务字段拒绝。
 * 执行时由 Conversation 按全局 → Chat → 本次 options 解析一次，再分发必要材料。
 * 凭据、SDK 对象和运行句柄不进入这些持久化设置或 Chat。
 */

/**
 * @typedef {object} GenerationOptions
 * @property {string} [connectionId] 后端连接身份，执行前必须解析存在且可用的连接。
 * @property {string} [modelId] 连接内的准确模型身份，不能仅凭显示名称推测能力。
 * @property {string} [language] 生成台词的语言。
 * @property {string | null} [translationLanguage] null 明确关闭翻译。
 * @property {number} [temperature] 有限值，范围由所选模型校验。
 * @property {number} [topP] (0, 1]；不支持时报告配置问题。
 * @property {number} [timeoutMs] 单次模型请求的正整数超时。
 * @property {number} [maxOutputTokens] 正整数输出上限。
 * @property {{ enabled: boolean, effort: string | null }} [reasoning] 按模型支持的推理强度校验。
 * @property {{ enabled: boolean, thresholdTokens: number, retainedTurns: number }} [compression] 阈值为正，至少保留最后一轮。
 * @property {number} [contextTokenBudget] 完整请求预算，不能放宽模型上限。
 * @property {{ maxSteps: number, maxRequestRetries: number, maxFormatRepairs: number }} [limits] 单轮有界循环；请求重试与格式修复分别计数，不限制跨轮自动续聊。
 * @property {{ system: string, output: string, summary: string }} [prompts] 三类提示材料；采样、超时不拼入提示词。
 */

/**
 * @typedef {object} SpeechSampling
 * @property {number} topK 正整数。
 * @property {number} topP (0, 1]。
 * @property {number} temperature 正的有限值。
 * @property {number} seed 整数，-1 表示随机种子。
 * @property {number} sampleSteps 正整数；按权重架构校验。
 */

/**
 * @typedef {object} SpeechOptions
 * @property {boolean} [enabled] Turn 开始时固定，只控制本轮自动合成。
 * @property {number} [speed] 正的有限倍率。
 * @property {number} [sentencePauseMs] 非负整数毫秒。
 * @property {SpeechSampling} [sampling] 整组覆盖，新任务固定使用。
 */

/**
 * @typedef {object} ScenarioSettings
 * @property {string} situation 情境，主动情景生成时必须非空。
 * @property {Object<string, { speakingStyle: string, interaction: string }>} characters 按参与角色 ID 保存，不可引用陌生角色。
 */

/**
 * @typedef {object} CharacterSelection
 * @property {string} formId 当前形态，显式无效值拒绝。
 * @property {Object<string, string | null>} modelOverrides 按形态 ID 保存模型覆盖；null 或缺少键表示使用该形态默认模型。
 */

/**
 * @typedef {object} ChatPresentationSettings
 * @property {import('./common.js').AssetRef | null} background 背景资源。
 * @property {Object<string, { x: number, y: number, scale: number, facing: 'left' | 'right' }>} characters 按角色 ID 保存位置、正缩放及朝向。
 */

/**
 * @typedef {object} ToolSelection
 * @property {string[]} enabled 用户选择的工具名；依赖由工具目录解析。
 */

/**
 * @typedef {object} ChatSettings
 * @property {GenerationOptions} generation 后续执行覆盖。
 * @property {SpeechOptions} speech 后续语音覆盖。
 * @property {ScenarioSettings | null} scenario 对话情景，不是可编辑的 Turn 设置。
 * @property {ToolSelection} tools 普通工具选择，不替代世界书开关。
 * @property {import('./worldbook.js').WorldbookInfo} worldbook 本 Chat 的知识范围设置。
 * @property {Object<string, CharacterSelection>} characters 按参与角色 ID 保存形态与模型选择。
 * @property {ChatPresentationSettings} presentation 场景偏好。
 */

/**
 * @typedef {object} TurnOptions
 * @property {string} requestId 一次操作的稳定去重身份；相同 ID 不允许更换输入或覆盖值。
 * @property {GenerationOptions} [generation] 仅本次覆盖，不自动复制到下一次重生成。
 * @property {SpeechOptions} [speech] 仅本次覆盖；其他 Chat 配置通过对应业务方法修改。
 */

/**
 * @typedef {object} SpeechStartupSettings
 * @property {'cpu' | 'cuda' | 'mps'} device 推理设备，下次进程启动生效。
 * @property {'float32' | 'float16'} precision 按设备与权重兼容性校验。
 * @property {string} architecture 模型结构配置，不能运行中切换。
 * @property {number} maxConcurrentInferences 正整数并发上限。
 * @property {number} maxResidentModels 正整数驻留实例预算。
 */

/**
 * @typedef {object} GlobalSettings
 * @property {number} revision 原子保存版本，新配置为 0。
 * @property {GenerationOptions} generation 生成默认值；执行前校验必需值完整，不猜连接。
 * @property {SpeechOptions} speech 语音默认值。
 * @property {SpeechStartupSettings} speechStartup 下次启动生效，不能改变在途任务。
 * @property {import('./presentation.js').BgmSettings} bgm 全局持久偏好；播放位置和错误是运行态。
 * @property {{ frameRate: number, theme: 'system' | 'light' | 'dark', startInPetMode: boolean }} display 正帧率及宿主展示偏好；窗口细节由宿主设计。
 */

export {}
