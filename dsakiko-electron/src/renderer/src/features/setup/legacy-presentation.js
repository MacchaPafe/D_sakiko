/** @typedef {{id: string, name: string, description: string}} LegacyCategory 四类整组导入项。 */
/** @type {LegacyCategory[]} */
export const legacyCategories = [
  {
    id: 'characters',
    name: '角色与人设',
    description: '保留角色设定、Live2D 模型、角色语音和你的人设'
  },
  { id: 'chats', name: '聊天记录', description: '带回历史对话及其中的图片、附件和语音' },
  { id: 'connections', name: '大模型连接', description: '沿用之前的大模型连接与模型选择' },
  { id: 'gsv', name: 'GPT-SoVITS 基础资源', description: '复用已有语音合成资源，减少后续下载' }
]
const warningMessages = {
  legacy_new_data_excluded: '资料位于新版数据目录，已跳过。',
  legacy_package_overlaps_data: '模型包内包含新版数据目录，已跳过该模型包。',
  legacy_upgrade_required: '旧版数据格式过早，请先用新版 Python 程序打开并保存。',
  legacy_document_invalid: '资料文件无法解析。',
  legacy_character_id_invalid: '角色目录名无法导入。',
  legacy_character_incomplete: '角色资料不完整，缺失内容将跳过。',
  legacy_path_outside_root: '引用位于所选目录之外，未读取这份资源。',
  legacy_file_missing: '引用的资源不存在。',
  legacy_resource_unreadable: '无法读取这份资源。',
  legacy_connections_pending: '部分大模型连接已保留，仍需配置。'
}
/** @param {object} warning 后端告警。 @returns {string} 可读说明，不展示内部错误堆栈。 */
export function legacyWarning(warning) {
  const message =
    warningMessages[warning.code] ?? warning.message ?? '部分资料未能完整转换，请核对导入结果。'
  const detail = warning.file ?? warning.count
  return detail ? `${message}（${detail}）` : message
}
/** @param {string} id 类别。 @param {object} scan 扫描摘要。 @returns {string} 真实数量，未知大小不使用设计示例。 */
export function legacyCount(id, scan) {
  if (id === 'characters') return `${scan.characters.length} 名角色 · ${scan.personaCount} 个人设`
  if (id === 'chats') return `${scan.chatCount} 段对话`
  if (id === 'connections')
    return `${scan.connectionCount} 个连接 · ${scan.pendingConnectionCount} 个待配置`
  return `${scan.gsvFileCount} 个文件`
}
/** @param {object} job 后端任务。 @returns {{completed: number, total: number, percent: number|null, label: string}} 按持久提交的资料数展示进度；文件复制单独说明。 */
export function legacyProgress(job) {
  const items = Object.values(job.items ?? {}).filter((item) => item.status !== 'skipped')
  const total = items.reduce((sum, item) => sum + item.total, 0)
  const completed = items.reduce((sum, item) => sum + Math.min(item.completed, item.total), 0)
  let label = `已导入 ${completed} / ${total} 条资料`
  if (job.status === 'running' && job.phase === 'importing')
    label = `正在准备文件 ${job.progress?.completed ?? 0} / ${job.progress?.total ?? 0}`
  if (job.status === 'running' && job.phase === 'validating') label = '正在核对旧版资料…'
  return { completed, total, percent: total ? Math.floor((completed / total) * 100) : null, label }
}
