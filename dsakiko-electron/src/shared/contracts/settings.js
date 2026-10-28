/**
 * 配置声明，不提供默认值实现。省略覆盖字段表示继承；数组整体替换，未知业务字段拒绝。
 * 执行时由 Conversation 按全局 → Chat → 本次 options 解析一次，再分发必要材料。
 * 凭据、SDK 对象和运行句柄不进入这些持久化设置或 Chat。
 * 提示词模板由 Agent 内部固定，不提供全局、Chat 或单轮模板覆盖。
 */

/**
 * @typedef {'Japanese' | 'Chinese'} GenerationLanguage
 */

/**
 * @typedef {object} GenerationOptions
 * @property {string} [connectionId] 后端连接身份，执行前必须解析存在且可用的连接。
 * @property {string} [modelId] 连接内的准确模型身份，不能仅凭显示名称推测能力。
 * @property {GenerationLanguage} [language] 生成台词的语言。
 * @property {GenerationLanguage | null} [translationLanguage] null 明确关闭翻译。
 * @property {number} [temperature] 有限值，范围由所选模型校验。
 * @property {number} [topP] (0, 1]；不支持时报告配置问题。
 * @property {number} [timeoutMs] 单次模型请求的正整数超时。
 * @property {number} [maxOutputTokens] 正整数输出上限。
 * @property {{ enabled: boolean, effort: string | null, budgetTokens?: number | null }} [reasoning] 按模型支持的强度校验；budgetTokens 用于手动思考预算，与 effort 互斥。
 * @property {{ enabled: boolean, thresholdTokens: number, retainedTurns: number }} [compression] 默认阈值 270000 随有效窗口按 90% 调整；自定义值为绝对阈值。retainedTurns 仅约束摘要，紧急裁剪可继续移除旧轮次。
 * @property {number} [contextTokenBudget] 未知模型窗口时的完整请求兜底预算；已知模型上限优先。
 * @property {{ maxSteps: number, maxRequestRetries: number, maxFormatRepairs: number }} [limits] 单轮有界循环；请求重试与格式修复分别计数，不限制跨轮自动续聊。
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
 * @property {string} formId 当前选中的形态，显式无效值拒绝。
 * @property {Object<string, string | null>} modelOverrides 按形态 ID 保存模型覆盖；模型必须属于对应形态的 models，null 或缺少键表示使用该形态默认模型。
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
 * @property {ScenarioSettings | null} scenario 对话情景，不是可编辑的 Turn 设置。只在 scripted-dialogue 模式下（小剧场）使用
 * @property {ToolSelection} tools 普通工具选择。该开关不影响世界书相关工具。
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
 * @typedef {object} GptSovitsStartupSettings
 * @property {import('./speech-resources.js').GptSovitsResourceManifest} resources 按用途组织的基础资源引用。
 * @property {'cpu' | 'cuda' | 'mps'} device 推理设备，下次进程启动生效。
 * @property {'float32' | 'float16'} precision 按设备与权重兼容性校验。
 * @property {string} architecture 模型结构配置，不能运行中切换。
 */

/**
 * @typedef {object} SpeechStartupSettings
 * @property {GptSovitsStartupSettings} gptSovits 当前本地引擎的专属配置。
 * @property {number} maxConcurrentInferences 正整数并发上限。
 * @property {number} maxResidentModels 正整数驻留实例预算。
 */

/**
 * @typedef {object} BuiltinToolSettings
 * @property {{readMode: 'all' | 'roots', readableRoots: string[], maxReadBytes: number}} files 全局读取范围与文本大小上限，workspace 始终可读；写入范围不由此放宽。
 * @property {{provider: string, baseUrl: string, timeoutMs: number, maxResults: number}} webSearch 搜索厂商与请求上限，密钥另存且不对外读取。
 * @property {{city: string, ipLocation: boolean}} weather 城市为空时可使用后端出口 IP 定位。
 */

/**
 * @typedef {object} GlobalSettings
 * @property {number} revision 原子保存版本，新配置为 0。
 * @property {BuiltinToolSettings} tools 内置工具的全局配置。
 * @property {GenerationOptions} generation 生成默认值；执行前校验必需值完整，不猜连接。
 * @property {SpeechOptions} speech 语音默认值。
 * @property {SpeechStartupSettings} speechStartup 下次启动生效，不能改变在途任务。
 * @property {import('./presentation.js').BgmSettings} bgm 曲目、开关及循环的全局持久偏好；音量由客户端保存，播放位置和错误是运行态。
 * @property {{ frameRate: number, theme: 'system' | 'light' | 'dark', startInPetMode: boolean }} display 正帧率及宿主展示偏好；窗口细节由宿主设计。
 */

/**
 * 全局生成配置的部分更新对象（嵌套分组内只需传入要修改的字段）。
 * @typedef {Partial<Omit<GenerationOptions, 'reasoning' | 'compression' | 'limits'>> & {reasoning?: Partial<GenerationOptions['reasoning']>, compression?: Partial<GenerationOptions['compression']>, limits?: Partial<GenerationOptions['limits']>}} GenerationUpdate
 */

/**
 * 批量更新全局设置时传入的部分配置对象。
 * @typedef {object} SettingsUpdate
 * @property {GenerationUpdate} [generation] 要修改的模型生成参数。
 * @property {Partial<SpeechOptions>} [speech] 要修改的语音合成参数。
 * @property {Partial<SpeechStartupSettings>} [speechStartup] 要修改的语音服务启动配置（下次启动语音进程时生效）。
 * @property {Partial<GlobalSettings['bgm']>} [bgm] 要修改的背景音乐设置。
 * @property {Partial<GlobalSettings['display']>} [display] 要修改的界面显示设置。
 */

export {}
