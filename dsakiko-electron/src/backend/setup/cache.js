import path from 'node:path'
import { readdir, unlink } from 'node:fs/promises'

/**
 * @param {string} directory 下载缓存目录。
 * @param {object[]} jobs 已读取的全部持久任务，调用者负责与任务创建、启动串行。
 * @param {Set<string>} activeIds 尚未退出执行函数的任务。
 * @returns {Promise<string[]>} 无可恢复任务使用的完整文件和分片。
 */
export async function unusedDownloadFiles(directory, jobs, activeIds = new Set()) {
  const retained = new Set()
  for (const job of jobs) {
    if (job.kind !== 'download') continue
    if (['succeeded', 'cancelled'].includes(job.status) && !activeIds.has(job.id)) continue
    for (const component of job.selected ?? [])
      for (const file of component.files) retained.add(file.sha256)
  }
  let entries
  try {
    entries = await readdir(directory, { withFileTypes: true })
  } catch (error) {
    if (error.code === 'ENOENT') return []
    throw error
  }
  return entries.flatMap((entry) => {
    const match = /^([a-f0-9]{64})(?:\.part)?$/.exec(entry.name)
    return match && entry.isFile() && !retained.has(match[1])
      ? [path.join(directory, entry.name)]
      : []
  })
}

/** 常规定期维护与用户清理使用同一套下载归属判定。 */
export async function collectDownloadCache(directory, jobs, activeIds) {
  for (const file of await unusedDownloadFiles(directory, jobs, activeIds)) {
    await unlink(file).catch((error) => {
      if (error.code !== 'ENOENT') throw error
    })
  }
}
