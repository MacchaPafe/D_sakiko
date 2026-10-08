/** 存档缺失警告与损坏恢复信息，权限/I/O/较新格式不等同于损坏。 */

/**
 * @typedef {object} ArchiveWarning
 * @property {string} chatId 来源或导入后 Chat 身份，按返回操作定义。
 * @property {string | null} entryId 关联消息/句身份，无具体消息则 null。
 * @property {'missing-image' | 'missing-audio' | 'missing-character' | 'missing-form' | 'lossy-import'} code 可处理的警告类别。
 * @property {string | null} resourceId 缺失资源或角色身份。
 * @property {string} message 可读说明，不伪造缺失内容。
 */

/**
 * @typedef {object} ArchiveDrafts
 * @property {import('./conversation.js').Chat[]} chats 已转换的新格式草稿，Conversation 分配新身份并置 revision 0。
 * @property {ArchiveWarning[]} warnings 缺失资源/有损导入说明。
 * @property {import('./common.js').AssetOwnerId} resourceOwner 临时资源持有者，建立新存档引用后释放；失败也需释放。
 */

/**
 * @typedef {object} ChatImportResult
 * @property {string[]} chatIds 已保存的新对话身份。
 * @property {ArchiveWarning[]} warnings 已映射为新身份的警告。
 */

/**
 * @typedef {object} CorruptArchive
 * @property {string} chatId 损坏的存档身份。
 * @property {string} recoveryId 本次检测的文件身份，恢复时重新核对，不能覆盖随后修改的文件。
 * @property {import('./common.js').Problem} problem 损坏诊断，不含原始堆栈。
 */

/**
 * @typedef {{ status: 'loaded', chat: import('./conversation.js').Chat, warnings: ArchiveWarning[] }
 *   | { status: 'missing' } | { status: 'corrupt', recovery: CorruptArchive }} ChatLoadResult
 */

export {}
