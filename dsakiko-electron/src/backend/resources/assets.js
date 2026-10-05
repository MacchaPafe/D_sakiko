import { createHash } from 'node:crypto'
import { copyFile, mkdir, readFile, realpath, stat } from 'node:fs/promises'
import { dirname, join, relative, resolve, extname, sep } from 'node:path'
import { readJson, writeJson, SerialQueue } from '../storage/files.js'
import { problemError } from '../../shared/contracts/desktop.js'

/** 原型资源寻址：外部目录只读，完成的音频保存在本工程数据目录。 */
export class Assets {
  queue = new SerialQueue()
  constructor(dataRoot, getExternalRoot) {
    this.dataRoot = dataRoot
    this.getExternalRoot = getExternalRoot
    this.registryPath = join(dataRoot, 'assets.json')
  }
  async initialize() {
    this.entries = await readJson(this.registryPath, {})
    return this
  }
  root(kind) {
    return kind === 'data' ? this.dataRoot : this.getExternalRoot()
  }
  async register(path, kind = 'external', bundle = false) {
    const root = this.root(kind)
    if (!root) throw problemError('missing_resources', '请先配置资源目录。')
    const canonical = await this.contained(root, path)
    const entry = {
      kind,
      path: relative(await realpath(root), canonical)
        .split(sep)
        .join('/'),
      bundle
    }
    const id = createHash('sha256').update(JSON.stringify(entry)).digest('hex').slice(0, 32)
    await this.queue.run('registry', async () => {
      if (this.entries[id]) return
      const next = { ...this.entries, [id]: entry }
      await writeJson(this.registryPath, next)
      this.entries = next
    })
    return { id }
  }
  async contained(root, path) {
    const base = await realpath(root)
    const candidate = await realpath(path)
    const rel = relative(base, candidate)
    if (rel === '..' || rel.startsWith(`..${sep}`) || resolve(base, rel) !== candidate)
      throw problemError('invalid_asset', '资源必须位于已配置的目录中。')
    return candidate
  }
  entry(asset) {
    const entry = this.entries[asset?.id]
    if (!entry) throw problemError('asset_unavailable', '资源引用不存在，请重新选择资源目录。')
    return entry
  }
  async localPath(asset) {
    const entry = this.entry(asset)
    return this.contained(this.root(entry.kind), join(this.root(entry.kind), entry.path))
  }
  async resolveAccess(asset) {
    const entry = this.entry(asset)
    await this.localPath(asset)
    const filename = entry.bundle ? entry.path.split('/').at(-1) : 'file' + extname(entry.path)
    return {
      url: `dsakiko-media://asset/${asset.id}/${encodeURIComponent(filename)}`,
      expiresAt: null
    }
  }
  async pathForUrl(url) {
    const parsed = new URL(url)
    if (parsed.protocol !== 'dsakiko-media:' || parsed.hostname !== 'asset')
      throw problemError('invalid_asset', '资源地址无效。')
    const [, id, ...parts] = parsed.pathname.split('/')
    const entry = this.entry({ id })
    const file = await this.localPath({ id })
    if (!entry.bundle) return file
    const decoded = parts.map(decodeURIComponent).join('/')
    // 只公开登记模型包内的文件，不能借模型相对路径访问配置与凭据。
    return this.contained(dirname(file), resolve(dirname(file), decoded))
  }
  async read(asset) {
    return new Uint8Array(await readFile(await this.localPath(asset)))
  }
  async importAudio(path, taskId) {
    const id = createHash('sha256').update(taskId).digest('hex')
    const target = join(this.dataRoot, 'audio', `${id}.wav`)
    await mkdir(dirname(target), { recursive: true })
    await copyFile(path, target)
    if ((await stat(target)).size <= 44) throw problemError('invalid_audio', '合成返回了空音频。')
    return this.register(target, 'data')
  }
}
