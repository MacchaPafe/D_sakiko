import path from 'node:path'
import { readdir, unlink } from 'node:fs/promises'

/**
 * @param {string} directory 下载缓存目录。
 * @param {object[]} jobs 已读取的全部持久任务，调用者负责与任务创建、启动串行。
 * @param {Set<string>} activeIds 尚未退出执行函数的任务。
 * @returns {Promise<void>} 回收无可恢复任务使用的完整文件和分片，保留未知条目。
 */
export async function collectDownloadCache(directory, jobs, activeIds = new Set()) {
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
    if (error.code === 'ENOENT') return
    throw error
  }
  for (const entry of entries) {
    const match = /^([a-f0-9]{64})(?:\.part)?$/.exec(entry.name)
    if (!match || !entry.isFile() || retained.has(match[1])) continue
    await unlink(path.join(directory, entry.name)).catch((error) => {
      if (error.code !== 'ENOENT') throw error
    })
  }
}
