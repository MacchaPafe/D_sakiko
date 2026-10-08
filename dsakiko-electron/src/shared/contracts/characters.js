/** 角色配置/派生能力分开；单形态也使用 forms，模型与形态不是同一个身份。 */

/**
 * @typedef {object} VoiceReference
 * @property {import('./common.js').AssetRef} audio 参考音频。
 * @property {string} text 参考文本。
 * @property {string} language 参考语言。
 */

/**
 * @typedef {object} FormVoice
 * @property {import('./common.js').AssetRef} gptWeights 当前形态 GPT 权重。
 * @property {import('./common.js').AssetRef} sovitsWeights 当前形态 SoVITS 权重。
 * @property {VoiceReference | null} defaultReference 同形态默认材料。
 * @property {Object<string, VoiceReference>} emotions 按情绪选择材料，不隐式跨形态降级。
 */

/**
 * @typedef {object} CharacterForm
 * @property {string} id 稳定形态身份。
 * @property {string} displayName 显示名，可为空。
 * @property {string} description 形态描述/语气，可为空。
 * @property {import('./common.js').AssetRef | null} avatar 头像。
 * @property {string | null} themeColor 主题色。
 * @property {string | null} defaultModelId 平级模型的默认指针，无模型时 null。
 * @property {FormVoice | null} voice 可选声音配置，失效引用仍保留。
 */

/**
 * @typedef {object} CharacterModel
 * @property {string} id 稳定模型身份，不由路径判断形态。
 * @property {string} name 展示名。
 * @property {string[]} formIds 可用于哪些形态。
 * @property {import('./presentation.js').ModelPresentation} presentation 入口及逻辑映射。
 */

/**
 * @typedef {object} CharacterDefinition
 * @property {string} id 稳定角色身份，不随显示名变化。
 * @property {number} revision 本次基于的版本，0 为新建。
 * @property {string} defaultFormId 必须指向 forms 中一项。
 * @property {CharacterForm[]} forms 非空且 ID 唯一，不限制为两项。
 * @property {CharacterModel[]} models 平级模型列表；默认模型只用指针。
 * @property {import('./worldbook.js').KnowledgeMapping[]} knowledgeMappings 跨该角色所有 Chat 共享的知识映射。
 * @property {import('./common.js').Metadata} extensions 未知扩展字段，不能替代正式配置。
 */

/**
 * @typedef {object} CharacterCapabilities
 * @property {string} id 角色身份。
 * @property {number} revision 实际解析版本。
 * @property {string} formId 实际形态。
 * @property {string} displayName 本次名称。
 * @property {string} description 本次描述/语气。
 * @property {import('./common.js').AssetRef | null} avatar 头像。
 * @property {string | null} themeColor 主题色。
 * @property {string | null} modelId 本次采用的模型。
 * @property {import('./presentation.js').ModelPresentation | null} presentation 经元数据检查的可选材料，不代表已成功加载。
 * @property {import('./presentation.js').PerformanceCatalog} performances 本次逻辑演出目录。
 * @property {FormVoice | null} voice 同次解析的声音配置快照，之后按句情绪选择，不读可变全局目录。
 * @property {import('./common.js').Problem[]} problems 未配置和已配置失效使用不同 code。
 */

/**
 * @typedef {object} VoiceResolution
 * @property {import('./speech.js').VoiceProfile | null} voice 完整权重/音频/文本/语言，不能只返回音频路径。
 * @property {'emotion' | 'default' | 'silent'} selected 先本形态情绪、后默认、最后无声。
 * @property {import('./common.js').Problem[]} problems 降级原因，不能静默换形态。
 */

export {}
