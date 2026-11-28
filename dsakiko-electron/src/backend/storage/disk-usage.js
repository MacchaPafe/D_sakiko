import path from 'node:path'
import { lstat, readdir } from 'node:fs/promises'

/**
 * 枚举普通文件。符号链接留在目录原处，避免把目标所在磁盘的容量算进来。
 * @param {string} target 已由宿主或资源模块确定的扫描路径。
 * @returns {AsyncGenerator<{file: string, info: import('node:fs').Stats}>} 文件与测量时的元数据。
 */
export async function* diskFiles(target) {
  const info = await lstat(target).catch((error) => {
    if (error.code === 'ENOENT') return null
    throw error
  })
  if (!info) return
  if (info.isFile()) yield { file: target, info }
  else if (info.isDirectory()) {
    const entries = await readdir(target).catch((error) => {
      if (error.code === 'ENOENT') return []
      throw error
    })
    for (const name of entries) yield* diskFiles(path.join(target, name))
  }
}

/**
 * 硬链接仍可能留在库外，因此预计释放量对共享 inode 按零估计。
 * @param {string} target 已确认归属的文件或目录。
 * @returns {Promise<number>} 可以计入预计释放量的文件字节。
 */
export async function reclaimableFileBytes(target) {
  let total = 0
  for await (const { info } of diskFiles(target)) if (info.nlink === 1) total += info.size
  return total
}

/**
 * 预览忽略本次将释放的持有者，实际删除仍由资源队列重新核对。
 * @param {import('../resources/interface.js').MediaAssets} assets 受管理资源的本机路径解析器。
 * @param {Awaited<ReturnType<import('../resources/interface.js').MediaAssets['state']>>} index 本次预览使用的索引快照。
 * @returns {{estimate: (ids: Iterable<string>, releasing?: Set<string>) => Promise<number>}} 同一快照下可复用的预计释放量计算。
 */
export function resourceUsage(assets, index) {
  const owners = new Map()
  for (const [owner, ids] of Object.entries(index.owners)) {
    for (const id of ids) {
      if (!owners.has(id)) owners.set(id, new Set())
      owners.get(id).add(owner)
    }
  }
  const sizes = new Map()
  return {
    async estimate(ids, releasing = new Set()) {
      let bytes = 0
      for (const id of new Set(ids)) {
        if (!index.assets[id] || [...(owners.get(id) ?? [])].some((owner) => !releasing.has(owner)))
          continue
        if (!sizes.has(id)) {
          const size = assets.collector
            .paths(index, id)
            .then(({ source }) => reclaimableFileBytes(source))
            .catch(() => 0)
          sizes.set(id, size)
        }
        bytes += await sizes.get(id)
      }
      return bytes
    }
  }
}
