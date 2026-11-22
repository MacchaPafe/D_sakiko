/** @typedef {import('../../backend/live2d-import/outfits.js').OutfitJob} OutfitJob */
/** @typedef {Pick<import('./setup.js').SetupSnapshot, 'id'|'kind'|'status'|'phase'|'completedAt'|'expiresAt'|'download'|'problem'>} ResourceJob */
/** @typedef {OutfitJob|ResourceJob|import('../../backend/backgrounds/interface.js').BackgroundJob} DownloadJob */
/** @typedef {{count: number}} DownloadSummary */
/** @typedef {{kind: 'snapshot'|'upsert', items: DownloadJob[]} | {kind: 'remove', id: string}} DownloadUpdate */

/** @param {DownloadJob} job 下载任务。 @returns {boolean} 是否计入下载中心的待处理数量。 */
export function isPendingDownload(job) {
  if ('kind' in job && !['download', 'background'].includes(job.kind)) return false
  return !['planned', 'succeeded', 'cancelled'].includes(job.status)
}
