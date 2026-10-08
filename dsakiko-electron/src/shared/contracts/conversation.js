/**
 * 嵌套存档是唯一权威历史。所有快照只读；字段身份在创建时固定，不用数组下标或投影时生成的 ID。
 * UserMessage.source 是输入来源的唯一权威；Turn.input 只定位输入，不再另存可能矛盾的 trigger。
 * 所有 Message、part、line 的 id 在 Chat 内唯一；callId 保留模型原值，在所属 Turn 内关联。
 */

/** @typedef {import('./common.js').Metadata} Metadata */
/** @typedef {import('./common.js').AssetRef} AssetRef */
/** @typedef {import('./common.js').JsonValue} JsonValue */
/** @typedef {'single-character' | 'scripted-dialogue'} DialogueMode 首期支持单角色和编剧式二人台词。 */

/**
 * @typedef {object} Attachment
 * @property {AssetRef} asset 本地持久资源，缺失时保留原引用。
 * @property {string} name 展示名称。
 * @property {string} mediaType 媒体类型；不代表模型已支持。
 */

/**
 * @typedef {object} UserSubmission
 * @property {string} text 输入文本，可为空但必须有有效图片；无文本且无有效附件才是空提交。
 * @property {Attachment[]} attachments 已完成本地导入的附件，不保存厂商文件 ID。
 */

/**
 * @typedef {object} AsyncResultItem
 * @property {string} requestId 可靠登记的交互请求身份，用于至多一次消费。
 * @property {string} sourceTurnId 发起交互的 Turn。
 * @property {string} callId 原调用 ID。
 * @property {string} toolName 原工具名称。
 * @property {'succeeded'} status 只有成功且有效的结果进入自动回复；失败和取消只结算请求。
 * @property {JsonValue} request 原问题或交互材料，保留问题与答案的关联。
 * @property {JsonValue} result 实际完成结果，成功允许 null。
 */

/**
 * @typedef {object} MessageBase
 * @property {string} id 稳定消息身份。
 * @property {Metadata} extensions 未知扩展保留，不用来隐藏必需业务字段。
 */

/**
 * 来源由不同受理入口赋值，普通发送方不能伪造 async 或 reminder。
 * @typedef {MessageBase & ({ kind: 'user', source: { kind: 'manual' }, content: UserSubmission }
 *   | { kind: 'user', source: { kind: 'async' }, results: AsyncResultItem[] }
 *   | { kind: 'user', source: { kind: 'reminder', reminderId: string, deliveryId: string }, text: string })} UserMessage
 */

/**
 * @typedef {object} SpokenContent
 * @property {string} text 原始发声文本，独立于展示翻译；清洗后为空则不合成。
 * @property {string} language 原发声语言，重合成不重新推断。
 * @property {Array<{ text: string, reading: string }>} pronunciationOverrides 已确定的读音语义。
 */

/**
 * @typedef {object} CharacterLine
 * @property {string} id 稳定句级身份，用于编辑、回放、合成和展示。
 * @property {string} speakerId 本轮允许发言的角色。
 * @property {string | null} formId 生成时固定的形态；仅资源缺失的导入记录可为 null，修复前不能猜形态合成。
 * @property {string} text 合法的完整台词，不随演出进度裁剪。
 * @property {string | null} translation 翻译。
 * @property {string | null} emotion 原情绪语义，重合成不重新推断。
 * @property {SpokenContent} spoken 固定发声语义；修改正文时由受限编辑操作同步更新。
 * @property {import('./presentation.js').PerformanceSelection} performance 逻辑动作与表情。
 * @property {import('./presentation.js').AudioMaterial | null} audio 已保存音频；修改发声材料后失效。
 * @property {Metadata} extensions 扩展数据。
 */

/**
 * @typedef {object} ReasoningPart
 * @property {string} id 稳定内容身份。
 * @property {'reasoning'} kind 实际返回的推理文本，不猜测缺失内容。
 * @property {string} text 完整正文，不进入语音或作为普通台词拼接。
 * @property {Metadata} protocol 供应商必要协议材料，按允许字段保存，不含 SDK 对象或凭据。
 */

/**
 * @typedef {object} ToolCallPart
 * @property {string} id 稳定内容身份。
 * @property {'tool-call'} kind 模型提出的调用，不在这里重复保存结果。
 * @property {string} callId 模型调用 ID；同一 Turn 中不能冲突。
 * @property {string} toolName 工具名。
 * @property {Metadata} arguments 模型提出的参数；执行前另做 Schema 和权限校验。
 * @property {Metadata} protocol 必需的供应商协议材料。
 */

/**
 * @typedef {object} DialoguePart
 * @property {string} id 稳定内容身份。
 * @property {'dialogue'} kind 已通过结构和业务校验的台词批次。
 * @property {CharacterLine[]} lines 原响应内有序台词，允许同角色连续发言。
 */

/** @typedef {ReasoningPart | ToolCallPart | DialoguePart} AssistantPart */

/**
 * 一次模型响应一个容器，保留 parts 顺序。格式修复的新响应另建容器，不移入旧响应冒充同一批。
 * 原响应中已接受的 reasoning 和调用保留；失败原文只作诊断，不伪造 DialoguePart。
 * @typedef {MessageBase & { kind: 'assistant', parts: AssistantPart[], protocol: Metadata }} AssistantMessage
 */

/**
 * @typedef {{ status: 'succeeded', value: JsonValue }
 *   | { status: 'accepted', requestId: string }
 *   | { status: 'failed', problem: import('./common.js').Problem }
 *   | { status: 'interrupted', problem: import('./common.js').Problem }} ToolOutcome
 */

/**
 * 每个调用至多一个协议结果。缺少结果不等于 succeeded/null；async/accepted 之后不补第二个结果。
 * interrupted 是运行器结算，表示结果不确定，不承诺外部副作用已撤销。
 * 更细的 not_executed/cancelled/outcome_unknown 编码仍是复核建议，本契约不将其设为必填枚举。
 * @typedef {MessageBase & { kind: 'tool-result', assistantMessageId: string, callId: string,
 *   origin: 'tool' | 'runner', outcome: ToolOutcome }} ToolResultMessage
 */

/**
 * 累计摘要只放在所覆盖最后一个完整 Turn 的末尾，位置是覆盖范围，不再保存终点或父摘要 ID。
 * @typedef {MessageBase & { kind: 'abstract', text: string }} AbstractMessage
 */

/** @typedef {UserMessage | AssistantMessage | ToolResultMessage | AbstractMessage} Message */

/**
 * 只保存脱敏的实际执行材料用于诊断，不据此恢复旧执行或决定重生成配置。
 * @typedef {object} TurnMeta
 * @property {string | null} requestId 受理去重身份；导入的旧记录可为 null。
 * @property {import('./settings.js').GenerationOptions} generation 本次解析后的生成值，不保存凭据。
 * @property {import('./settings.js').SpeechOptions} speech 本轮固定的自动语音设置。
 * @property {import('./settings.js').ScenarioSettings | null} scenario 实际采用的情景快照。
 * @property {Object<string, import('./settings.js').CharacterSelection>} characters 实际采用的形态与模型。
 * @property {string[]} tools 解析依赖后本轮工具集合。
 * @property {import('./worldbook.js').WorldbookScope | null} worldbook 本次知识范围。
 * @property {import('./worldbook.js').WorldbookDiagnostic[]} worldbookDiagnostics 实际检索诊断，不进入模型历史。
 * @property {Metadata} extensions 迁移原文或可选诊断，不参与普通上下文。
 */

/**
 * @typedef {object} Turn
 * @property {string} id 稳定 UUID；重生成分配新 ID，不复用旧执行。
 * @property {{ kind: 'message', messageId: string } | { kind: 'scenario' }} input 定位本轮 UserMessage，或无用户消息的情景生成。
 * @property {TurnMeta} meta 本次执行记录；running 和轮次组不持久化。
 * @property {Message[]} messages 有序过程；包括多次响应及独立结果，最多一份置于末尾的摘要。
 */

/**
 * @typedef {object} Reminder
 * @property {string} id 稳定提醒身份。
 * @property {string} dueAt ISO 8601 到期时间。
 * @property {string} instruction 内部输入。
 * @property {'enabled' | 'disabled' | 'delivered' | 'expired'} state 到期后忙碌延后，超过到期 12 小时仍未投递则过期。
 */

/**
 * @typedef {object} ChatMeta
 * @property {import('./settings.js').ChatSettings} settings 类型明确的对话设置。
 * @property {Reminder[]} reminders 持久化提醒，普通 async 组失效不删除提醒。
 * @property {Metadata} extensions 未知扩展字段。
 */

/**
 * Chat 是数据；Conversation 是唯一运行时修改所有者。保存版本由 Store 原子比较并递增。
 * @typedef {object} Chat
 * @property {string} id 稳定对话身份。
 * @property {number} revision 所基于的存档版本，新建 0，首次保存 1；与格式版本分开。
 * @property {string} title 标题。
 * @property {string[]} characterIds 固定参与角色。
 * @property {import('./persona.js').UserPersonaSnapshot | null} userPersona 创建时固定，重生成、分支、导入不重查目录。
 * @property {DialogueMode} mode 编排方式，不以模式阻止后端未来接受连续输入。
 * @property {ChatMeta} meta 对话配置与提醒。
 * @property {Turn[]} turns 完整有序历史。
 */

/**
 * @typedef {object} LineRef
 * @property {string} turnId 所属 Turn。
 * @property {string} messageId 所属 AssistantMessage。
 * @property {string} partId 所属 DialoguePart。
 * @property {string} lineId 句级稳定身份。
 */

/**
 * @typedef {object} MessageDisplay
 * @property {string} entryId 由 Message、part 或 line 的已有身份派生；不能使用数组下标。
 * @property {'pending' | 'progress' | 'full'} mode 只有台词参与逐字演出；其他内容全文展示。
 * @property {number} revealedCharacters 当前可见字符数；停止后的全文不能被迟到进度覆盖。
 */

/**
 * @typedef {object} TurnExecution
 * @property {string} turnId 已知轮次；未知回调拒收，不用回调重建映射。
 * @property {'running' | 'finished' | 'terminated'} state finished 等待生成、自动语音及普通演出全部结算。
 * @property {'generation' | 'speech' | 'presentation' | null} phase 面向用户的当前阶段，不替代生命周期。
 * @property {import('./common.js').Problem | null} problem 终止原因；单句音频错误另外报告。
 */

/**
 * @typedef {object} ConversationSnapshot
 * @property {Chat} chat 只读权威内容，保存失败时可包含尚未落盘的内容。
 * @property {number} revision 运行态观察版本，与 chat.revision 分开。
 * @property {'idle' | 'generating' | 'presenting' | 'compressing'} activity 当前活动。
 * @property {string | null} activeTurnId 本轮身份；历史回放不激活历史 Turn。
 * @property {TurnExecution[]} executions 当前运行环境已知的生命周期；重启不恢复旧映射。
 * @property {{ state: 'saved' | 'saving' | 'failed', problem: import('./common.js').Problem | null }} persistence 保存状态，与执行成功失败分开。
 * @property {{ canGenerate: boolean, canEditHistory: boolean, reasons: string[] }} admission 实际受理限制，包含忙碌、压缩和保存故障。
 * @property {boolean} selected 是否选中。
 * @property {MessageDisplay[]} display 只读展示进度。
 * @property {Array<{ line: LineRef, problem: import('./common.js').Problem }>} audioProblems 单句错误不取消整个轮次组。
 * @property {import('./common.js').Problem | null} problem 最近需要处理的问题。
 */

/**
 * @typedef {object} ChatSummary
 * @property {string} id 对话身份。
 * @property {string} title 标题。
 * @property {string[]} characterIds 参与角色。
 * @property {DialogueMode} mode 编排方式。
 */

export {}
