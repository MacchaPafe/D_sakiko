/**
 * @typedef {'asr'|'gsv'|'voice.anon'|'voice.soyo'|'voice.sakiko'} ResourceChoice
 * @typedef {{characters?: boolean, connections?: boolean, chats?: boolean, gsv?: boolean}} LegacySelection
 * @typedef {{kind: 'legacy', scanId: string, include?: LegacySelection} | {kind: 'download', selection: ResourceChoice[], accelerator?: 'cpu'|'cuda'}} SetupPlanRequest
 */

/**
 * @typedef {object} SetupSnapshot
 * @property {string} id 计划和任务共用的持久身份。
 * @property {'legacy'|'download'} kind 资料导入或附加资源下载。
 * @property {'planned'|'running'|'paused'|'failed'|'cancelled'|'succeeded'} status 当前任务状态。
 * @property {string} phase 当前阶段，重连以整个快照为准。
 * @property {number} expiresAt 尚未执行的计划有效期；暂停任务不按此字段删除。
 * @property {{logical: number, added: number, download: number, cache: number}} space 逻辑大小、预计新增资源占用、下载量和缓存占用。
 * @property {Array<{requiredBytes: number, availableBytes: number}>} volumes 按实际卷合并的执行空间检查。
 * @property {{hardlinkFiles: number, copyFiles: number, reusedFiles: number}} transfer 已验证的传输方式统计。
 * @property {object|null} progress 当前文件或组件的进度。
 * @property {object[]} results 已提交项目。
 * @property {Record<string, {status: string, total: number, completed: number, problem?: import('./common.js').Problem|null}>|null} items 旧版导入的分类提交状态。
 * @property {{bytes: number, totalBytes: number, bytesPerSecond: number|null, remainingSeconds: number|null, components: Array<{id: string, status: string, problem: import('./common.js').Problem|null}>}|null} download 下载任务的累计进度与安装阶段。
 * @property {object[]} warnings 缺失或未转换项目，不包含连接凭据。
 * @property {import('./common.js').Problem|null} problem 可展示的失败信息。
 */

/**
 * @typedef {object} OnboardingState
 * @property {1} version 初始化状态版本。
 * @property {'quick'|'legacy'|null} path 初始化路径。
 * @property {'welcome'|'preparing'|'importing'|'optional-resources'|'complete'} stage 当前阶段。
 * @property {string|null} jobId 旧版导入任务。
 * @property {boolean} initialized 是否已允许进入主页。
 * @property {boolean} noticeAcknowledged 是否已确认连接配置提示。
 * @property {'builtin'|'configured'|'missing'} connectionState 当前选中连接及模型的本地配置状态。
 * @property {'configure-model-connection'|'model-connection-missing'|null} notice 主页需展示的提示。
 */
export {}
