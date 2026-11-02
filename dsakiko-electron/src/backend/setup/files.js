import path from 'node:path'
import { createHash } from 'node:crypto'
import { createReadStream, createWriteStream } from 'node:fs'
import { lstat, realpath, mkdir, statfs, readdir } from 'node:fs/promises'
import { pipeline } from 'node:stream/promises'
import { requireValue } from '../core/support.js'

/** 仅允许可在三个目标平台表示的相对文件名，拒绝 Windows 盘符、设备名及 ADS。 */
export function relativeFile(value) {
  requireValue(
    typeof value === 'string' &&
      value.length > 0 &&
      !/[\\:]/.test(value) &&
      ![...value].some((char) => char.charCodeAt(0) < 32),
    'unsafe_resource_path'
  )
  requireValue(
    value
      .split('/')
      .every(
        (part) =>
          part &&
          !['.', '..'].includes(part) &&
          !/[. ]$/.test(part) &&
          !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part)
      ),
    'unsafe_resource_path'
  )
  return value
}

/** 读取来源前逐级拒绝符号链接；不允许所选目录借链接访问外部资料。 */
export async function sourcePath(root, name) {
  relativeFile(name)
  let file = await realpath(root)
  for (const part of name.split('/')) {
    file = path.join(file, part)
    requireValue(
      !(await lstat(file)).isSymbolicLink(),
      'source_symlink',
      '来源包含符号链接，请先将资源放入旧版本目录。'
    )
  }
  return file
}

/** 扫描目录中的普通文件，不跟随链接，不读取平台生成的元数据。 */
export async function walk(root, prefix = '') {
  const result = []
  for (const entry of await readdir(path.join(root, prefix), { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name === '__pycache__') continue
    const name = prefix ? prefix + '/' + entry.name : entry.name
    relativeFile(name)
    requireValue(!entry.isSymbolicLink(), 'source_symlink', '来源包含符号链接。')
    if (entry.isDirectory()) result.push(...(await walk(root, name)))
    else if (entry.isFile()) result.push(name)
  }
  return result.sort()
}

/** 比较内容位置和修改时间；硬链接会改变 ctime，因此不将它当作源内容变化。 */
export async function stamp(file) {
  const value = await lstat(file)
  requireValue(value.isFile(), 'source_not_file')
  return { size: value.size, mtime: value.mtimeMs, ino: value.ino, dev: value.dev }
}

/** 流式校验大文件，不把权重放入 JS 堆。 */
export async function sha256(file, signal) {
  const hash = createHash('sha256')
  await pipeline(createReadStream(file), hash, { signal })
  return hash.digest('hex')
}

/** 按实际所在卷合计新增空间，避免两个根目录同卷时分别比较造成误判。 */
export async function checkSpace(requirements) {
  const volumes = new Map()
  for (const { directory, bytes } of requirements) {
    await mkdir(directory, { recursive: true })
    const [disk, info] = await Promise.all([statfs(directory), lstat(directory)])
    const current = volumes.get(info.dev) ?? {
      requiredBytes: 0,
      availableBytes: disk.bavail * disk.bsize
    }
    current.requiredBytes += bytes
    volumes.set(info.dev, current)
  }
  const result = [...volumes.values()]
  requireValue(
    result.every((item) => item.availableBytes >= item.requiredBytes),
    'insufficient_disk_space',
    '磁盘剩余空间不足，请释放空间后重试。'
  )
  return result
}

/** 文件复制可取消；只用于新路径，绝不截断已建立的硬链接。 */
export async function copyNew(source, destination, signal) {
  await mkdir(path.dirname(destination), { recursive: true })
  await pipeline(
    createReadStream(source),
    createWriteStream(destination, { flags: 'wx', mode: 0o600 }),
    { signal }
  )
}
