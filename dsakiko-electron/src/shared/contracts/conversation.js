/**
 * 对话持久化数据和界面投影。消息身份与临时演出编号分开保存。
 * 字段描述核心形状；不在这里规定数据库、文件路径或存储格式。
 */

/** @typedef {import('./common.js').Metadata} Metadata */
/** @typedef {import('./common.js').AssetRef} AssetRef */
/** @typedef {import('./common.js').JsonValue} JsonValue */
/** @typedef {'single-character' | 'scripted-dialogue'} DialogueMode 第一版编剧模式限定二人；轮流生成策略不在此写死。 */

/**
 * @typedef {object} Attachment
 * @property {AssetRef} asset 附件资源。
 * @property {string} name 展示名称。
 * @property {string} mediaType 媒体类型。
 */

/**
 * @typedef {object} UserSubmission
 * @property {string} text 输入文本。
 * @property {Attachment[]} attachments 应用本地资源导入已完成的附件，不保存厂商文件编号；远端材料在请求前准备。
 */

/**
 * 一次工具调用的可保存记录。开始和结束使用同一 callId，由 Conversation 更新同一条工具消息。
 * interrupted 表示调用方未取得确定结果，不表示外部工具的副作用已撤回。
 * @typedef {object} ToolCallRecord
 * @property {string} callId 一次工具调用的稳定唯一编号，不以工具名称代替。
 * @property {string} toolName 工具名称。
 * @property {Metadata} arguments 已校验的调用参数。
 * @property {'running' | 'succeeded' | 'failed' | 'interrupted'} status 调用状态；停止生成或恢复存档时不能遗留虚假的 running 状态。
 * @property {JsonValue} result 成功时的工具结果；尚无结果时为 null，成功结果本身也允许为 null。
 * @property {import('./common.js').Problem | null} error 失败说明；无错误时为 null，不保存异常实例或堆栈。
 */

/**
 * Message 记录整个对话过程；ContextMessage 和 GeneratedMessage 仅是临时输入输出，不单独持久化。
 * 对于 tool 类的消息，attachments 通常为 []；speakerId、translation、audio、emotion、performance 通常为 null。
 * 工具消息用于记录和展示，不进入语音合成或演出；text 是简短说明，参数和结果使用 toolCall。
 * 每段模型实际返回的 reasoning 单独保存一条消息，text 保存正文，不拼入角色台词或复制到每句台词。
 * reasoning 的 attachments 为 []；speakerId、translation、audio、emotion、performance、toolCall 均为 null。
 * reasoning 只保存和展示，不进入语音或演出；Conversation 补齐 id 与 metadata 等存档字段。
 * @typedef {object} Message
 * @property {string} id 稳定消息身份。
 * @property {'user' | 'character' | 'tool' | 'reasoning'} kind 对话记录类别，只有 character 台词参与演出。
 * @property {string | null} speakerId 角色消息使用角色身份；其他消息可为 null。
 * @property {string} text 已提交的完整文本，不随演出进度截断。
 * @property {string | null} translation 翻译。
 * @property {Attachment[]} attachments 附件。
 * @property {import('./presentation.js').AudioMaterial | null} audio 已保存的语音材料。
 * @property {string | null} emotion 生成的情绪提示。
 * @property {import('./presentation.js').PerformanceSelection | null} performance 角色演出选择。
 * @property {ToolCallRecord | null} toolCall 新工具消息必须提供调用记录；其他类型为 null，旧存档缺少详情时也保留为 null。
 * @property {Metadata} metadata 扩展消息数据，包括迁移时保留的旧字段。
 */

/**
 * @typedef {object} Turn
 * @property {string} id 稳定轮次身份；重新生成的执行编号不复用这个字段。
 * @property {{ kind: 'user' | 'reminder' | 'continuation', sourceId: string | null }} trigger 触发来源，内部提醒不冒充用户输入。
 * @property {Metadata} metadata 本轮冻结的设置和扩展上下文，例如世界书快照。
 * @property {Message[]} messages 按生成过程顺序保存 user、reasoning、tool 与 character；工具终态更新原消息，不改变其位置。
 */

/**
 * @typedef {object} Reminder
 * @property {string} id 稳定提醒身份，用于去重。
 * @property {string} dueAt 计划触发时间。
 * @property {string} instruction 提供给本对话的内部输入。
 * @property {'enabled' | 'disabled' | 'delivered'} state 本对话忙时保留 enabled 并延迟触发。
 */

/**
 * Chat 是可持久化的对话数据，Conversation 是持有并推进它的运行对象；运行任务不进入 Chat。
 * 本地业务修改保留所基于的 revision，由 ChatStore 在成功保存时递增并返回新快照。
 * 一次保存包含多项修改也只递增一次；播放进度不影响此版本，存档格式版本另行管理。
 * @typedef {object} Chat
 * @property {string} id 稳定对话身份。
 * @property {number} revision 非负整数，表示快照所基于的持久化版本；0 为尚未保存，新建成功为 1，后续成功提交逐次递增。
 * @property {string} title 显示标题。
 * @property {string[]} characterIds 参与角色的稳定身份。
 * @property {import('./persona.js').UserPersonaSnapshot | null} userPersona 创建时固定的用户身份；null 表示无自定义人格，来源修改或缺失不影响本 Chat。
 * @property {DialogueMode} mode 对话编排方式。
 * @property {{ defaults: Metadata, reminders: Reminder[], extensions: Metadata }} meta 对话默认设置、提醒和扩展数据。
 * @property {Turn[]} turns 已提交轮次。
 */

/**
 * @typedef {object} MessageDisplay
 * @property {string} messageId 对应持久化消息。
 * @property {'progress' | 'full'} mode 只有 character 可跟随演出使用 progress；其他类型和停止播放后使用 full，迟到进度不能重新隐藏文本。
 * @property {number} revealedCharacters 当前应展示的字符数。
 */

/**
 * @typedef {object} ConversationSnapshot
 * @property {Chat} chat 对话只读快照；界面通过 turns 中的 reasoning.text 和工具消息的 toolCall 展示详情，不直接订阅 Agent。
 * @property {number} revision 运行态投影版本，与 chat.revision 不同。
 * @property {'idle' | 'generating' | 'presenting'} activity 面向用户的当前活动，不暴露内部调度队列。
 * @property {string | null} activeTurnId 正在生成或演出的轮次。
 * @property {boolean} selected 当前应用是否选择此对话。
 * @property {MessageDisplay[]} display 文本显示投影，包含必要的演出进度映射。
 * @property {import('./common.js').Problem | null} problem 最近需要用户处理的问题。
 */

/**
 * @typedef {object} ChatSummary
 * @property {string} id 对话身份。
 * @property {string} title 标题。
 * @property {string[]} characterIds 参与角色。
 * @property {DialogueMode} mode 编排方式。
 */

export {}
