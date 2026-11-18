import path from 'node:path'
import { createWriteStream } from 'node:fs'
import { lstat, mkdir, rename, rm, writeFile } from 'node:fs/promises'
import { Readable, Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { problem, requireValue } from '../core/support.js'
import { downloadFile, resourceFetch } from '../setup/download.js'
import { relativeFile } from '../setup/files.js'
import { modelPackageLimit } from './interface.js'

const bestdori = 'https://bestdori.com'
const ttl = 20 * 60 * 1000
const manifestLimit = 16 * 1024 * 1024
const fileLimit = 128 * 1024 * 1024
const thumbnailLimit = 2 * 1024 * 1024
const sourceId = /^[A-Za-z0-9_-]{1,150}$/u

/** @typedef {'bestdori'|'modelscope'} OutfitSource 在线服装来源。 */
/** @typedef {{source: OutfitSource, id: string, characterId: string, name: string, version: 'v2'|'v3', thumbnail?: string, path?: string, sizeBytes?: number, sha256?: string}} OnlineOutfit 已校验的在线服装条目，下载路径仅由来源适配器使用。 */

function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
}

async function readLimited(response, limit) {
  requireValue(
    response.ok && response.body,
    'live2d_source_unavailable',
    '服装资源请求失败，请稍后重试。'
  )
  const chunks = []
  let size = 0
  for await (const chunk of Readable.fromWeb(response.body)) {
    size += chunk.length
    requireValue(size <= limit, 'live2d_source_too_large', '服装资源超过下载限制。')
    chunks.push(chunk)
  }
  return Buffer.concat(chunks)
}

function artwork(name) {
  const relative = relativeFile(name)
  requireValue(
    /\.(png|jpe?g|webp)$/iu.test(relative),
    'invalid_live2d_thumbnail',
    '缩略图格式不受支持。'
  )
  return relative
}

async function json(response, limit = manifestLimit) {
  const bytes = await readLimited(response, limit)
  try {
    return JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/u, ''))
  } catch {
    requireValue(false, 'invalid_live2d_catalog', '服装来源返回的目录或模型清单格式有误。')
  }
}

function validateHostedCatalog(data) {
  requireValue(
    object(data) &&
      data.schemaVersion === 1 &&
      Array.isArray(data.items) &&
      data.items.length <= 10000,
    'invalid_live2d_catalog',
    'V3 服装清单格式不正确。'
  )
  const seen = new Set()
  return data.items.map((row) => {
    requireValue(
      object(row) &&
        sourceId.test(row.id) &&
        sourceId.test(row.characterId) &&
        typeof row.id === 'string' &&
        typeof row.characterId === 'string' &&
        typeof row.name === 'string' &&
        row.name.trim() &&
        row.name.length <= 120 &&
        Number.isSafeInteger(row.sizeBytes) &&
        row.sizeBytes > 0 &&
        row.sizeBytes <= modelPackageLimit &&
        typeof row.sha256 === 'string' &&
        /^[a-f0-9]{64}$/u.test(row.sha256),
      'invalid_live2d_catalog',
      'V3 服装清单包含无效的服装信息。'
    )
    const identity = row.characterId + ':' + row.id
    requireValue(!seen.has(identity), 'invalid_live2d_catalog', 'V3 服装清单中存在重复 ID。')
    seen.add(identity)
    const filename = relativeFile(row.path)
    requireValue(
      filename.toLowerCase().endsWith('.zip'),
      'invalid_live2d_catalog',
      'V3 服装应发布为 ZIP。'
    )
    return {
      source: 'modelscope',
      version: 'v3',
      id: row.id,
      characterId: row.characterId,
      name: row.name.trim(),
      path: filename,
      sizeBytes: row.sizeBytes,
      sha256: row.sha256,
      ...(row.thumbnail && { thumbnail: artwork(row.thumbnail) })
    }
  })
}

function bestdoriFiles(data) {
  const base = data?.Base
  requireValue(
    object(base?.model) &&
      Array.isArray(base.textures) &&
      base.textures.length > 0 &&
      Array.isArray(base.motions) &&
      Array.isArray(base.expressions),
    'invalid_bestdori_build',
    'Bestdori 模型清单缺少必要文件。'
  )
  const files = new Map()
  function add(item, kind, directory, extension) {
    requireValue(
      typeof item?.fileName === 'string' && typeof item.bundleName === 'string',
      'invalid_bestdori_build',
      'Bestdori 模型清单中的文件信息不正确。'
    )
    let name = kind === 'physics' ? item.fileName : item.fileName.replace(/\.bytes$/u, '')
    if (extension && !/\.(png|jpe?g)$/iu.test(name)) name += extension
    relativeFile(name)
    requireValue(!name.includes('/') && name.length <= 180, 'invalid_bestdori_build')
    const bundle = relativeFile(item.bundleName)
    requireValue(bundle.startsWith('live2d/'), 'invalid_bestdori_build')
    const file = directory ? directory + '/' + name : name
    const remote = 'assets/jp/' + bundle + '_rip/' + name
    requireValue(!files.has(file) || files.get(file).remote === remote, 'invalid_bestdori_build')
    files.set(file, { kind, file, remote })
  }
  add(base.model, 'model', '')
  if (base.physics?.fileName && base.physics?.bundleName) add(base.physics, 'physics', '')
  for (const item of base.textures) add(item, 'texture', 'textures', '.png')
  for (const item of base.motions) add(item, 'motion', 'motions')
  for (const item of base.expressions) add(item, 'expression', 'expressions')
  requireValue(files.size <= 1000, 'invalid_bestdori_build', 'Bestdori 模型包含过多文件。')
  return [...files.values()]
}

function v2Model(files) {
  const model = {
    version: '3.1',
    layout: { center_x: 0, center_y: 0, width: 2 },
    hit_areas_custom: {
      body_x: [-0.3, 0.2],
      body_y: [0.3, -1.9],
      head_x: [-0.25, 1],
      head_y: [0.25, 0.2]
    },
    model: files.find((item) => item.kind === 'model').file,
    textures: files.filter((item) => item.kind === 'texture').map((item) => item.file),
    motions: Object.create(null),
    expressions: []
  }
  for (const { kind, file } of files) {
    if (kind === 'motion') {
      const name = path.posix.basename(file).replace(/\.(?:mtn|json)$/iu, '')
      const category = name.replace(/[^\p{L}]/gu, '') || 'motion'
      ;(model.motions[category] ??= []).push({ name, file })
    } else if (kind === 'expression') {
      model.expressions.push({
        name: path.posix.basename(file).replace(/(?:\.exp)?\.json$/iu, ''),
        file
      })
    } else if (kind === 'physics') model.physics = file
  }
  return model
}

function bestdoriPath(relative) {
  return relativeFile(relative).split('/').map(encodeURIComponent).join('/')
}

const bestdoriBasicOutfitNames = new Map([
  ['casual', '常服'],
  ['casual_summer', '夏季常服'],
  ['casual_winter', '冬季常服'],
  ['school_summer', '夏季校服'],
  ['school_winter', '冬季校服']
])

function bestdoriOutfitName(id, description) {
  // 简体缺失时沿用日文等目录名称。剧情常服有时只有资源编号，已知类型可以据此展示。
  const catalogName = Array.isArray(description)
    ? [description[3], ...description].find((value) => typeof value === 'string' && value.trim())
    : null
  if (catalogName) return catalogName.trim().slice(0, 120)

  const match = /^\d{3}_([a-z_]+)(?:-(\d{4}))?$/u.exec(id)
  if (!match) return id
  const name = bestdoriBasicOutfitNames.get(match[1])
  if (!name) return id
  return match[2] ? name + '（' + match[2] + '）' : name
}

/** 在线目录、缩略图和模型文件仅访问各来源固定的 HTTPS 地址。 */
export class OutfitSources {
  constructor(transport = fetch) {
    this.fetch = transport
    this.cached = new Map()
  }

  async cachedCatalog(key, load, refresh) {
    const last = this.cached.get(key)
    if (!refresh && last && last.expires > Date.now()) return last.promise
    const promise = Promise.resolve().then(load)
    this.cached.set(key, { expires: Date.now() + ttl, promise })
    try {
      return await promise
    } catch (error) {
      if (this.cached.get(key)?.promise === promise) this.cached.delete(key)
      throw error
    }
  }

  async bestdoriFetch(relative, signal) {
    const timeout = AbortSignal.timeout(45000)
    return this.fetch(bestdori + '/' + bestdoriPath(relative), {
      redirect: 'error',
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout
    })
  }

  async v2(character, refresh) {
    if (character.bestdoriId === null) return []
    const [assets, costumes] = await this.cachedCatalog(
      'bestdori',
      async () => {
        const responses = await Promise.all([
          this.bestdoriFetch('api/explorer/jp/assets/_info.json'),
          this.bestdoriFetch('api/costumes/all.5.json')
        ])
        const [index, metadata] = await Promise.all(responses.map((response) => json(response)))
        requireValue(object(index?.live2d?.chara) && object(metadata), 'invalid_bestdori_catalog')
        return [
          index.live2d.chara,
          new Map(
            Object.entries(metadata).map(([id, costume]) => [
              costume?.assetBundleName,
              { id, costume }
            ])
          )
        ]
      },
      refresh
    )
    const prefix = String(character.bestdoriId).padStart(3, '0') + '_'
    const list = []
    for (const id of Object.keys(assets)) {
      if (!id.startsWith(prefix) || id.endsWith('general') || !sourceId.test(id)) continue
      const metadata = costumes.get(id)
      const name = bestdoriOutfitName(id, metadata?.costume?.description)
      const thumbnail =
        metadata && /^\d+$/u.test(metadata.id)
          ? 'assets/jp/thumb/costume/group' +
            Math.floor(Number(metadata.id) / 50) +
            '_rip/' +
            id +
            '.png'
          : null
      list.push({
        source: 'bestdori',
        id,
        characterId: character.id,
        name,
        version: 'v2',
        ...(thumbnail && { thumbnail })
      })
    }
    return list.sort(
      (left, right) =>
        Number(left.id.endsWith('live_event')) - Number(right.id.endsWith('live_event')) ||
        left.id.localeCompare(right.id, 'en', { numeric: true })
    )
  }

  async v3(character, refresh) {
    const entries = await this.cachedCatalog(
      'modelscope',
      async () => {
        const response = await resourceFetch('live2d', 'catalog.json', {
          fetch: this.fetch,
          signal: AbortSignal.timeout(30000)
        })
        if (response.status === 404) {
          await response.body?.cancel()
          return []
        }
        return validateHostedCatalog(await json(response))
      },
      refresh
    )
    return entries.filter((item) => item.characterId === character.id)
  }

  /**
   * @param {import('../characters/bang-dream.js').BangDreamCharacter} character 已确认的目录身份。
   * @param {boolean} refresh 是否跳过目录短期缓存。
   * @returns {Promise<{items: OnlineOutfit[], problems: object[]}>} 可用来源的服装和各自错误。
   */
  async catalog(character, refresh = false) {
    const results = await Promise.allSettled([
      this.v2(character, refresh),
      this.v3(character, refresh)
    ])
    return {
      items: results.flatMap((item) => (item.status === 'fulfilled' ? item.value : [])),
      problems: results.flatMap((item, index) =>
        item.status === 'rejected'
          ? [{ ...problem(item.reason), source: index === 0 ? 'bestdori' : 'modelscope' }]
          : []
      )
    }
  }

  async thumbnail(item, signal) {
    if (!item.thumbnail) return null
    const response =
      item.source === 'bestdori'
        ? await this.bestdoriFetch(item.thumbnail, signal)
        : await resourceFetch('live2d', item.thumbnail, { fetch: this.fetch, signal })
    const bytes = await readLimited(response, thumbnailLimit)
    if (bytes.subarray(0, 8).toString('hex') === '89504e470d0a1a0a') return { bytes, type: 'png' }
    if (bytes.subarray(0, 3).toString('hex') === 'ffd8ff') return { bytes, type: 'jpeg' }
    if (bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WEBP')
      return { bytes, type: 'webp' }
    return null
  }

  /**
   * @param {OnlineOutfit} item 已登记在目录的 V3 服装。
   * @param {string} directory 任务下载缓存。
   * @param {AbortSignal} signal 任务取消。
   * @param {(completed: number, total: number) => void} progress 下载进度。
   * @returns {Promise<string>} 下载完成并校验的 ZIP 路径。
   */
  async downloadArchive(item, directory, signal, progress) {
    return downloadFile({ ...item, source: 'live2d' }, directory, {
      fetch: this.fetch,
      signal,
      progress: (completed) => progress(completed, item.sizeBytes)
    })
  }

  /**
   * @param {OnlineOutfit} item 已登记在目录的 V2 服装。
   * @param {string} directory 源文件缓存，暂停后按完整文件续传。
   * @param {AbortSignal} signal 任务取消。
   * @param {(completed: number, total: number) => void} progress 文件数进度。
   * @returns {Promise<string>} 已生成模型入口的目录。
   */
  async downloadV2(item, directory, signal, progress) {
    const build = await json(
      await this.bestdoriFetch(
        'assets/jp/live2d/chara/' + item.id + '_rip/buildData.asset',
        signal
      ),
      4 * 1024 * 1024
    )
    const files = bestdoriFiles(build)
    const present = []
    let completed = 0
    progress(completed, files.length)
    for (let index = 0; index < files.length; index += 4) {
      const results = await Promise.allSettled(
        files.slice(index, index + 4).map(async (file) => {
          if (await this.downloadV2File(file, directory, signal)) present.push(file)
          progress(++completed, files.length)
        })
      )
      const failed = results.find((result) => result.status === 'rejected')
      if (failed) throw failed.reason
    }
    signal.throwIfAborted()
    const model = v2Model(files.filter((file) => present.includes(file)))
    await writeFile(path.join(directory, 'model.json'), JSON.stringify(model, null, 2) + '\n', {
      signal
    })
    const thumbnail = await this.thumbnail(item, signal).catch(() => null)
    if (thumbnail?.type === 'png')
      await writeFile(path.join(directory, 'icon.png'), thumbnail.bytes, { signal })
    signal.throwIfAborted()
    return directory
  }

  async downloadV2File(file, directory, signal) {
    const target = path.join(directory, file.file)
    if (
      (
        await lstat(target).catch((error) => {
          if (error.code !== 'ENOENT') throw error
          return null
        })
      )?.isFile()
    )
      return true
    const response = await this.bestdoriFetch(file.remote, signal)
    if (response.status === 404 && file.kind === 'physics') {
      await response.body?.cancel()
      return false
    }
    requireValue(
      response.ok && response.body,
      'bestdori_file_unavailable',
      'Bestdori 模型文件下载失败：' + file.file
    )
    await mkdir(path.dirname(target), { recursive: true })
    const partial = target + '.part'
    let total = 0
    const counter = new Transform({
      transform(chunk, _encoding, callback) {
        total += chunk.length
        if (total > fileLimit) return callback(new Error('Bestdori 模型文件超过大小限制。'))
        callback(null, chunk)
      }
    })
    try {
      await pipeline(
        Readable.fromWeb(response.body),
        counter,
        createWriteStream(partial, { mode: 0o600 }),
        { signal }
      )
      requireValue(total > 0, 'bestdori_file_unavailable', 'Bestdori 返回了空文件：' + file.file)
      await rename(partial, target)
    } finally {
      await rm(partial, { force: true })
    }
    return true
  }
}
