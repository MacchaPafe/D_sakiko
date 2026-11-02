import path from 'node:path'
import { mkdir, stat, rm, rename } from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { Readable, Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { failure, requireValue } from '../core/support.js'
import { sha256, relativeFile } from './files.js'

const sources = {
  voices:
    'https://www.modelscope.cn/datasets/Jerry4787/custom-gsv-model/resolve/master/character-voices/',
  asr: 'https://www.modelscope.cn/datasets/Jerry4787/custom-gsv-model/resolve/master/asr-whisper-small-q8/',
  gsv: 'https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/',
  runtimes: 'https://www.modelscope.cn/datasets/Jerry4787/custom-gsv-model/resolve/master/runtimes/'
}

/** 资源地址只由已编译的来源和相对路径构造，不接受客户端或清单提供任意 URL。 */
export function resourceUrl(source, name) {
  requireValue(Object.hasOwn(sources, source), 'resource_source_denied')
  return sources[source] + relativeFile(name).split('/').map(encodeURIComponent).join('/')
}

function allowedRedirect(url) {
  const value = new URL(url)
  return (
    value.protocol === 'https:' &&
    !value.username &&
    !value.password &&
    ([
      'www.modelscope.cn',
      'modelscope.cn',
      'cdn-lfs-cn-1.modelscope.cn',
      'cdn-lfs-cn-2.modelscope.cn'
    ].includes(value.hostname) ||
      /^cdn-lfs-[a-z0-9-]+\.modelscope\.cn$/.test(value.hostname) ||
      /^modelscope-[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com$/.test(value.hostname))
  )
}

/** 手动处理跳转，每跳重新校验来源且不附带任何上传凭据。 */
export async function resourceFetch(
  source,
  name,
  { fetch: transport = fetch, signal, headers = {} } = {}
) {
  let url = resourceUrl(source, name)
  for (let redirects = 0; redirects < 8; redirects += 1) {
    const response = await transport(url, { signal, headers, redirect: 'manual' })
    if (![301, 302, 303, 307, 308].includes(response.status)) return response
    const next = new URL(response.headers.get('location'), url)
    await response.body?.cancel()
    requireValue(allowedRedirect(next), 'resource_redirect_denied', '资源重定向地址不受支持。')
    url = next.href
  }
  throw failure('resource_redirect_denied', '资源重定向次数过多。')
}

/** 固定哈希决定分片身份；完整文件验证成功后才原子交付。 */
export async function downloadFile(
  file,
  directory,
  { fetch: transport = fetch, signal, progress = () => {} } = {}
) {
  await mkdir(directory, { recursive: true })
  const destination = path.join(directory, file.sha256)
  const partial = destination + '.part'
  const exists = async (name) =>
    stat(name).catch((error) => {
      if (error.code !== 'ENOENT') throw error
      return null
    })
  const complete = await exists(destination)
  if (complete?.size === file.sizeBytes && (await sha256(destination, signal)) === file.sha256)
    return destination
  await rm(destination, { force: true })
  let offset = (await exists(partial))?.size ?? 0
  if (offset > file.sizeBytes) {
    await rm(partial, { force: true })
    offset = 0
  }
  if (offset < file.sizeBytes) {
    const response = await resourceFetch(file.source, file.path, {
      fetch: transport,
      signal,
      headers: offset
        ? { Range: `bytes=${offset}-`, 'Accept-Encoding': 'identity' }
        : { 'Accept-Encoding': 'identity' }
    })
    requireValue(
      response.status === 200 || response.status === 206,
      'resource_download_failed',
      '资源下载失败，请稍后重试。'
    )
    if (response.status === 206)
      requireValue(
        response.headers.get('content-range') ===
          `bytes ${offset}-${file.sizeBytes - 1}/${file.sizeBytes}`,
        'resource_changed',
        '远端资源发生变化，请刷新资源目录后重试。'
      )
    else offset = 0
    let received = offset
    const counter = new Transform({
      transform(chunk, encoding, callback) {
        received += chunk.length
        if (received > file.sizeBytes)
          return callback(failure('resource_changed', '远端文件大小已改变，请刷新资源目录。'))
        progress(received)
        callback(null, chunk)
      }
    })
    await pipeline(
      Readable.fromWeb(response.body),
      counter,
      createWriteStream(partial, { flags: offset ? 'a' : 'w', mode: 0o600 }),
      { signal }
    )
  }
  if (
    (await stat(partial)).size !== file.sizeBytes ||
    (await sha256(partial, signal)) !== file.sha256
  ) {
    await rm(partial, { force: true })
    throw failure('resource_hash_mismatch', '资源校验失败，可能已被更新；请刷新资源目录后重试。')
  }
  await rename(partial, destination)
  return destination
}
