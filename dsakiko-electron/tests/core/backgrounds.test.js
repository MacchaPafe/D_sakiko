import { mkdtemp, readdir, rm, utimes, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { afterEach, expect, test, vi } from 'vitest'
import { Backgrounds } from '../../src/backend/backgrounds/interface.js'
import { BackgroundPreviewCache } from '../../src/backend/backgrounds/preview-cache.js'
import { MediaAssets } from '../../src/backend/resources/interface.js'

const key = 'images/backgrounds/classroom.png'
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XKZkAAAAASUVORK5CYII=',
  'base64'
)
const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

async function fixture(getFile) {
  const root = await mkdtemp(path.join(tmpdir(), 'stage-backgrounds-'))
  cleanups.push(() => rm(root, { recursive: true, force: true }))
  const roots = {
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache')
  }
  const assets = new MediaAssets(roots)
  const transport = vi.fn((url, options) => {
    expect(options.headers.Authorization).toBe('Bearer test-download')
    expect(options.redirect).toBe('error')
    expect(new URL(url).origin).toBe('https://models.dsakiko.org')
    if (url.endsWith('/api/images?kind=background'))
      return Response.json({ items: [{ r2_key: key, kind: 'background', name: '教室' }] })
    if (url.endsWith('/images/background-previews/classroom.png')) return new Response(png)
    expect(url.endsWith('/' + key)).toBe(true)
    return getFile ? getFile(options.signal) : new Response(png)
  })
  const options = { ...roots, assets, fetch: transport, token: 'test-download' }
  const backgrounds = new Backgrounds(options)
  await assets.initialize()
  await backgrounds.initialize()
  cleanups.push(() => backgrounds.close())
  return { assets, backgrounds, options }
}

test('在线目录只下载被请求的缩略图，相同请求合并且重启后仍可复用', async () => {
  const { backgrounds, options } = await fixture()
  const directory = path.join(options.cacheRoot, 'background-previews')
  await backgrounds.catalog()
  await expect(readdir(directory)).rejects.toMatchObject({ code: 'ENOENT' })
  const expected = 'data:image/png;base64,' + png.toString('base64')
  await expect(Promise.all([backgrounds.preview(key), backgrounds.preview(key)])).resolves.toEqual([
    expected,
    expected
  ])
  expect(options.fetch).toHaveBeenCalledTimes(2)
  expect(await readdir(directory)).toHaveLength(1)
  await backgrounds.close()

  const restored = new Backgrounds(options)
  await restored.initialize()
  cleanups.push(() => restored.close())
  await expect(restored.preview(key)).resolves.toBe(expected)
  expect(options.fetch.mock.calls.slice(2).map(([url]) => new URL(url).pathname)).toEqual([
    '/api/images'
  ])
})

test('并发写入按磁盘容量淘汰最早的缩略图，忽略损坏的缓存', async () => {
  const { backgrounds, options } = await fixture()
  const directory = path.join(options.cacheRoot, 'background-previews')
  await backgrounds.preview(key)
  const [oldest] = await readdir(directory)
  const past = new Date('2021-01-01T00:00:00Z')
  await utimes(path.join(directory, oldest), past, past)
  const cache = new BackgroundPreviewCache(directory, png.length * 2)
  const names = ['a', 'b'].map((char) => char.repeat(64) + '.png')
  await Promise.all(names.map((name) => cache.write(name, png)))
  expect((await readdir(directory)).sort()).toEqual(names)

  await writeFile(path.join(directory, oldest), '损坏的图片')
  const restored = new Backgrounds(options)
  await restored.initialize()
  cleanups.push(() => restored.close())
  await expect(restored.preview(key)).resolves.toBe(
    'data:image/png;base64,' + png.toString('base64')
  )
  expect(
    options.fetch.mock.calls.filter(([url]) => url.includes('/background-previews/'))
  ).toHaveLength(2)
})

test('每张在线原图只有一个任务，图库移除后仍由对话持有，最后一个引用释放才回收', async () => {
  const { backgrounds, assets } = await fixture()
  expect(await backgrounds.preview(key)).toBe('data:image/png;base64,' + png.toString('base64'))
  const [a, b] = await Promise.all([backgrounds.start(key), backgrounds.start(key)])
  expect(a.id).toBe(b.id)
  await vi.waitFor(() => expect(backgrounds.data.jobs[0].status).toBe('succeeded'))
  expect(backgrounds.data.items).toHaveLength(1)
  const { asset } = backgrounds.data.items[0]
  await backgrounds.rename(asset.id, '旧校舍')
  expect(backgrounds.data.items[0]).toMatchObject({ name: '旧校舍', filename: 'classroom.png' })
  await assets.addReferences('chat:example', [asset])
  await backgrounds.remove(asset.id)
  await backgrounds.maintain()
  expect(await assets.read(asset)).toEqual(png)
  await assets.releaseOwner('chat:example')
  await backgrounds.maintain()
  expect(backgrounds.data.removed).toEqual([])
  await expect(assets.inspect(asset)).rejects.toThrow()
})

test('暂停、重启与重试保留任务身份，失败的图片不会进入图库', async () => {
  let attempt = 0
  const { backgrounds, options } = await fixture((signal) => {
    attempt++
    if (attempt === 1)
      return new Promise((_, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason), { once: true })
      )
    return new Response(attempt === 2 ? Buffer.from('invalid') : png)
  })
  const { id } = await backgrounds.start(key)
  await backgrounds.control(id, 'pause')
  expect(backgrounds.data.jobs[0].status).toBe('paused')
  await backgrounds.close()
  const restored = new Backgrounds(options)
  await restored.initialize()
  cleanups.push(() => restored.close())
  await restored.control(id, 'resume')
  await vi.waitFor(() => expect(restored.data.jobs[0].status).toBe('failed'))
  expect(restored.data.items).toEqual([])
  await restored.control(id, 'resume')
  await vi.waitFor(() => expect(restored.data.jobs[0].status).toBe('succeeded'))
  expect(restored.data.items).toHaveLength(1)
})

test('多选导入先校验整批图片，失败可重试，不产生半批图库记录', async () => {
  const { assets, backgrounds } = await fixture()
  await assets.using({}, async (owner) => {
    const source = { kind: 'bytes', bytes: png, name: 'classroom.png', mediaType: 'image/png' }
    const a = await assets.importAsset(source, owner, 'chat-image')
    const b = await assets.importAsset({ ...source, name: 'hall.png' }, owner, 'chat-image')
    const invalid = await assets.importAsset(
      { ...source, bytes: Buffer.from('no') },
      owner,
      'chat-image'
    )
    await expect(backgrounds.importImages([a.asset, invalid.asset])).rejects.toThrow()
    expect(backgrounds.data.items).toHaveLength(0)
    await backgrounds.importImages([a.asset, b.asset])
    await backgrounds.importImages([a.asset, b.asset])
    expect(backgrounds.data.items.map((row) => row.filename)).toEqual(['classroom.png', 'hall.png'])
  })
})
