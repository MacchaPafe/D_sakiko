import { createHash } from 'node:crypto'
import { join } from 'node:path'
import { failure, requireValue } from '../core/support.js'
import {
  BackgroundPreviewCache,
  backgroundPreviewCacheDirectory,
  previewImageLimit
} from './preview-cache.js'

const origin = 'https://models.dsakiko.org'
const png = Buffer.from('89504e470d0a1a0a', 'hex')
const prefix = 'images/backgrounds/'
const imageLimit = 32 * 1024 * 1024

/** @param {Buffer} bytes 图片内容。 @returns {string|null} 支持的图片格式。 */
export function imageType(bytes) {
  if (bytes.subarray(0, 8).equals(png)) return 'image/png'
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return 'image/jpeg'
  if (bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP')
    return 'image/webp'
  return null
}

/** @typedef {{key: string, name: string, filename: string, sha256: string|null}} OnlineBackground 自有图库中的原图与可选校验值。 */

/** 自有资源服务的下载凭据仅在后端使用，所有目录和图片均从固定域名读取。 */
export class BackgroundSource {
  constructor(
    transport,
    {
      cacheRoot,
      token = process.env.DSAKIKO_DOWNLOAD_TOKEN || import.meta.env?.DSAKIKO_BUNDLED_DOWNLOAD_TOKEN
    } = {}
  ) {
    this.fetch = transport
    this.token = token
    this.previewCache = cacheRoot
      ? new BackgroundPreviewCache(join(cacheRoot, backgroundPreviewCacheDirectory))
      : null
    this.previews = new Map()
    this.previewBytes = 0
    this.previewRequests = new Map()
  }

  async bytes(path, limit, signal, progress) {
    requireValue(this.token, 'background_token_missing', '未配置背景下载凭据，请使用完整发行包。')
    const timeout = AbortSignal.timeout(45000)
    try {
      const response = await this.fetch(origin + path, {
        headers: {
          Authorization: 'Bearer ' + this.token,
          'User-Agent': 'D-Sakiko-Downloader/2.0',
          'Accept-Encoding': 'identity'
        },
        redirect: 'error',
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout
      })
      const errors = {
        401: '下载凭据无效',
        403: '资源服务拒绝访问',
        404: '资源不存在',
        429: '请求过于频繁，请稍后重试'
      }
      requireValue(
        response.status === 200 && response.body,
        'background_unavailable',
        errors[response.status] ?? '背景资源暂时不可用，请重试。'
      )
      const total = response.headers.get('content-encoding')
        ? 0
        : Number(response.headers.get('content-length')) || 0
      const chunks = []
      let completed = 0
      for await (const chunk of response.body) {
        completed += chunk.length
        requireValue(completed <= limit, 'background_too_large', '图片或目录超过大小限制。')
        chunks.push(chunk)
        progress?.({ completed, total })
      }
      requireValue(
        completed && (!total || total === completed),
        'background_incomplete',
        '背景资源不完整，请重试。'
      )
      return Buffer.concat(chunks)
    } catch (error) {
      if (error.problem || signal?.aborted) throw error
      throw failure('background_network', '背景资源连接失败或超时，请重试。')
    }
  }

  async load() {
    const bytes = await this.bytes('/api/images?kind=background', 4 * 1024 * 1024)
    const invalid = () => failure('background_catalog_invalid', '背景目录格式有误，请刷新重试。')
    let data
    try {
      data = JSON.parse(bytes.toString())
    } catch {
      throw invalid()
    }
    if (!Array.isArray(data?.items) || data.items.length > 10000) throw invalid()
    const seen = new Set()
    return data.items.map((row) => {
      const key = row?.r2_key
      if (
        row?.kind !== 'background' ||
        typeof key !== 'string' ||
        !key.startsWith(prefix) ||
        !key.endsWith('.png') ||
        !key.split('/').every((part) => /^[\w.-]+$/u.test(part) && !part.endsWith('.')) ||
        seen.has(key) ||
        (row.sha256 != null && !/^[a-f0-9]{64}$/u.test(row.sha256))
      )
        throw invalid()
      seen.add(key)
      const filename = key.split('/').at(-1)
      return {
        key,
        filename,
        name: (typeof row.name === 'string' && row.name.trim()) || filename,
        sha256: row.sha256 ?? null
      }
    })
  }

  async catalog(refresh = false) {
    if (!this.cache || refresh || Date.now() > this.expires) {
      const request = this.load()
      this.cache = request
      this.expires = Date.now() + 20 * 60 * 1000
      request.catch(() => {
        if (this.cache === request) this.cache = null
      })
    }
    return this.cache
  }

  async item(key) {
    const item = (await this.catalog()).find((entry) => entry.key === key)
    requireValue(item, 'background_missing', '目录中找不到这张背景。')
    return item
  }

  async preview(key) {
    const item = await this.item(key)
    const name =
      createHash('sha256')
        .update(JSON.stringify([item.key, item.sha256]))
        .digest('hex') + '.png'
    const cached = this.previews.get(name)
    if (cached && cached.expires > Date.now()) return cached.url
    const pending = this.previewRequests.get(name)
    if (pending) return pending
    const request = this.loadPreview(item, name).finally(() => this.previewRequests.delete(name))
    this.previewRequests.set(name, request)
    return request
  }

  async loadPreview(item, name) {
    const generation = this.previewCache?.generation
    let bytes = await this.previewCache?.read(name).catch(() => null)
    if (!bytes || imageType(bytes) !== 'image/png') {
      bytes = await this.bytes(
        '/' + item.key.replace(prefix, 'images/background-previews/'),
        previewImageLimit
      )
      requireValue(imageType(bytes) === 'image/png', 'invalid_image', '缩略图格式有误。')
      // 缓存目录不可写或已满时，本次下载的图片仍可正常显示。
      await this.previewCache?.write(name, bytes, generation).catch(() => {})
    }
    const url = 'data:image/png;base64,' + bytes.toString('base64')
    // 搜索和切页会反复挂载卡片，保留近期已编码的结果以减少磁盘读取和重复编码。
    const previous = this.previews.get(name)
    if (previous) this.previewBytes -= previous.url.length
    this.previews.delete(name)
    this.previews.set(name, { url, expires: Date.now() + 6 * 3600000 })
    this.previewBytes += url.length
    while (this.previewBytes > 12 * 1024 * 1024) {
      const oldest = this.previews.keys().next().value
      this.previewBytes -= this.previews.get(oldest).url.length
      this.previews.delete(oldest)
    }
    return url
  }

  async download(item, signal, progress) {
    const bytes = await this.bytes('/' + item.key, imageLimit, signal, progress)
    requireValue(imageType(bytes) === 'image/png', 'invalid_image', '下载的文件不是 PNG 图片。')
    requireValue(
      !item.sha256 || createHash('sha256').update(bytes).digest('hex') === item.sha256,
      'background_checksum',
      '背景图片校验失败，请重试。'
    )
    return bytes
  }
}
