import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  symlink,
  writeFile
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, expect, test } from 'vitest'
import { MediaAssets } from '../../src/backend/resources/interface.js'
import { ChatStore } from '../../src/backend/storage/interface.js'
import { startHttpServer } from '../../src/server/http-server.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'
import { SpeechProcess } from '../../src/main/processes/speech-process.js'
import { resolveDataDirectories, validateDataDirectories } from '../../src/main/data-directories.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

async function fixture() {
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'dsakiko-resources-')))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const roots = {
    appDataRoot: path.join(directory, 'application'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  }
  const assets = new MediaAssets(roots)
  const store = new ChatStore({ directory: path.join(roots.appDataRoot, 'chats'), assets })
  return { directory, roots, assets, store }
}

function source(name, text = name) {
  return { kind: 'bytes', name, mediaType: 'application/octet-stream', bytes: Buffer.from(text) }
}

function chat(resources) {
  return {
    id: 'chat',
    revision: 0,
    title: '历史',
    characterIds: [],
    mode: 'single-character',
    turns: [],
    meta: { reminders: [] },
    extensions: { resources }
  }
}

test('三个目录统一登记，重启后按引用读取，公开信息不包含路径', async () => {
  const { roots, assets } = await fixture()
  const image = await assets.importAsset(source('picture.png'), 'draft', 'chat-image')
  const audio = await assets.importAsset(source('voice.wav'), 'task', 'audio')
  const temporary = await assets.importAsset(source('preview'), 'task', 'temporary')
  expect(await assets.localPath(image.asset)).toBe(
    path.join(roots.appDataRoot, 'assets/files', image.asset.id)
  )
  expect(await assets.localPath(audio.asset)).toBe(
    path.join(roots.resourceLibraryRoot, 'audio', audio.asset.id)
  )
  expect(await assets.localPath(temporary.asset)).toBe(
    path.join(roots.cacheRoot, 'assets', temporary.asset.id)
  )
  const restarted = new MediaAssets(roots)
  for (const info of [image, audio, temporary]) {
    expect(Buffer.from(await restarted.read(info.asset)).toString()).toBe(info.name)
    expect(await restarted.inspect(info.asset)).toEqual(info)
    expect(JSON.stringify(info)).not.toContain(roots.appDataRoot)
    expect(info).not.toHaveProperty('location')
  }
  expect(await readdir(path.join(roots.appDataRoot, 'assets'))).toEqual(['files', 'index.json'])
  await expect(assets.importAsset(source('unknown'), 'draft')).rejects.toHaveProperty(
    'problem.code',
    'invalid_asset_purpose'
  )
  await expect(assets.collectUnused()).rejects.toHaveProperty('problem.code', 'cleanup_unavailable')
  expect(Buffer.from(await assets.read(image.asset)).toString()).toBe('picture.png')
})

test('同一聊天跨目录保存与归档恢复，缓存资源不能进入存档', async () => {
  const { assets, store } = await fixture()
  const image = await assets.importAsset(source('image.png'), 'draft', 'chat-image')
  const audio = await assets.importAsset(source('audio.wav'), 'task', 'audio')
  const saved = await store.save(chat([image.asset, audio.asset]))
  const archive = await store.exportArchive([saved.id])
  expect((await assets.inspect(archive)).purpose).toBe('archive')
  const restored = await store.readArchive(archive)
  const refs = restored.chats[0].extensions.resources
  expect(refs).not.toEqual([image.asset, audio.asset])
  expect((await assets.inspect(refs[0])).purpose).toBe('chat-image')
  expect((await assets.inspect(refs[1])).purpose).toBe('audio')
  expect(Buffer.from(await assets.read(refs[0])).toString()).toBe('image.png')
  const pending = await assets.importAsset(source('pending'), 'task', 'temporary')
  await expect(
    store.save({ ...saved, extensions: { resources: [pending.asset] } })
  ).rejects.toHaveProperty('problem.code', 'temporary_asset_reference')
  expect((await store.load(saved.id)).chat).toEqual(saved)
})

test('资源库离线或图片缺失时保留聊天、登记与引用', async () => {
  const { roots, assets, store } = await fixture()
  const image = await assets.importAsset(source('image.png'), 'draft', 'chat-image')
  const audio = await assets.importAsset(source('audio.wav'), 'task', 'audio')
  const saved = await store.save(chat([image.asset, audio.asset]))
  await rm(await assets.localPath(image.asset))
  await rename(roots.resourceLibraryRoot, roots.resourceLibraryRoot + '-offline')
  for (const info of [image, audio]) {
    expect(await assets.inspect(info.asset)).toEqual(info)
    await expect(assets.read(info.asset)).rejects.toHaveProperty(
      'problem.code',
      'asset_unavailable'
    )
  }
  expect((await store.load(saved.id)).chat).toEqual(saved)
  expect((await store.save({ ...saved, title: '仍能保存文本' })).extensions.resources).toEqual([
    image.asset,
    audio.asset
  ])
})

test('模型目录保留相对路径，拒绝越界成员、符号链接和递归导入', async () => {
  const { directory, roots, assets } = await fixture()
  const model = path.join(directory, 'source-model')
  await mkdir(path.join(model, 'textures'), { recursive: true })
  await writeFile(path.join(model, 'model.model3.json'), '{"texture":"textures/body.png"}')
  await writeFile(path.join(model, 'textures/body.png'), 'texture')
  const imported = await assets.importAsset(
    { kind: 'local-directory', path: model, entry: 'model.model3.json' },
    'character',
    'live2d'
  )
  expect(await assets.localPath(imported.asset)).toBe(
    path.join(roots.resourceLibraryRoot, 'live2d', imported.asset.id, 'model.model3.json')
  )
  expect(Buffer.from(await assets.read(imported.asset, 'textures/body.png')).toString()).toBe(
    'texture'
  )
  for (const member of ['../index.json', '/etc/passwd', 'textures/../../escape', 'C:\\escape'])
    await expect(assets.read(imported.asset, member)).rejects.toHaveProperty(
      'problem.code',
      'invalid_asset_path'
    )
  await symlink(path.join(directory, 'outside'), path.join(model, 'link'))
  await expect(
    assets.importAsset(
      { kind: 'local-directory', path: model, entry: 'model.model3.json' },
      'draft',
      'live2d'
    )
  ).rejects.toHaveProperty('problem.code', 'invalid_asset_package')
  expect(await readdir(path.join(roots.resourceLibraryRoot, 'live2d'))).toEqual([imported.asset.id])
  await expect(
    assets.importAsset(
      { kind: 'local-directory', path: roots.resourceLibraryRoot },
      'draft',
      'model'
    )
  ).rejects.toHaveProperty('problem.code', 'invalid_asset_package')
})

test('旧索引和损坏索引不会被当成空数据覆盖', async () => {
  const { roots, assets } = await fixture()
  const index = path.join(roots.appDataRoot, 'assets/index.json')
  await mkdir(path.dirname(index), { recursive: true })
  for (const text of [
    '{"assets":{},"owners":{}}',
    '{invalid',
    '{"version":2,"assets":{},"locations":{},"owners":[]}'
  ]) {
    await writeFile(index, text)
    await expect(assets.importAsset(source('image.png'), 'draft', 'chat-image')).rejects.toThrow()
    expect(await readFile(index, 'utf8')).toBe(text)
    expect(await readdir(path.dirname(index))).toEqual(['index.json'])
  }
})

test('HTTP 上传按已校验用途路由，图片和音频统一授权读取', async () => {
  const { assets, roots } = await fixture()
  const server = await startHttpServer({ assets })
  cleanups.push(() => server.close())
  const client = createBackendClient(server)
  cleanups.push(() => client.close())
  for (const purpose of ['chat-image', 'audio']) {
    const info = await client.assets.upload({
      name: purpose,
      mediaType: 'application/octet-stream',
      base64: Buffer.from(purpose).toString('base64'),
      purpose
    })
    const access = await client.assets.access(info.asset)
    expect(await (await fetch(access.url)).text()).toBe(purpose)
    expect(JSON.stringify(await client.assets.inspect(info.asset))).not.toContain(roots.appDataRoot)
  }
  await expect(
    client.assets.upload({
      name: 'bad',
      mediaType: 'image/png',
      base64: '',
      purpose: '../application'
    })
  ).rejects.toThrow()
  await expect(
    client.assets.upload({ name: 'bad', mediaType: 'image/png', base64: '', purpose: 'temporary' })
  ).rejects.toThrow()
})

test('系统默认目录与三个独立覆盖值，安装包和重叠路径被拒绝', async () => {
  const { directory, roots } = await fixture()
  const mac = resolveDataDirectories({ platform: 'darwin', home: '/Users/example', env: {} })
  expect(mac.appDataRoot).toBe('/Users/example/Library/Application Support/D_Sakiko/core-v1')
  expect(mac.cacheRoot).toBe('/Users/example/Library/Caches/D_Sakiko')
  const win = resolveDataDirectories({
    platform: 'win32',
    home: 'C:\\Users\\example',
    env: { LOCALAPPDATA: 'D:\\Profile\\Local' }
  })
  expect(win.resourceLibraryRoot).toBe('D:\\Profile\\Local\\D_Sakiko\\library')
  const custom = resolveDataDirectories({
    platform: process.platform,
    home: directory,
    env: {
      LOCALAPPDATA: directory,
      DSAKIKO_DATA_DIR: roots.appDataRoot,
      DSAKIKO_RESOURCE_LIBRARY_DIR: roots.resourceLibraryRoot,
      DSAKIKO_CACHE_DIR: roots.cacheRoot
    }
  })
  expect(custom).toEqual(roots)
  validateDataDirectories(roots, [])
  const bundle = path.join(directory, 'Example.app')
  await mkdir(bundle)
  expect(() =>
    validateDataDirectories({ ...roots, cacheRoot: path.join(bundle, 'cache') }, [bundle])
  ).toThrow()
  const alias = path.join(directory, 'bundle-alias')
  await symlink(bundle, alias)
  expect(() =>
    validateDataDirectories({ ...roots, cacheRoot: path.join(alias, 'cache') }, [bundle])
  ).toThrow()
  expect(() =>
    validateDataDirectories({ ...roots, cacheRoot: path.join(roots.appDataRoot, 'cache') }, [])
  ).toThrow()
})

test('真实 Python 语音进程在缓存目录启动并完成握手，不加载模型', async () => {
  const { roots } = await fixture()
  const modelRoot = path.join(roots.resourceLibraryRoot, 'models')
  await mkdir(path.join(modelRoot, 'GPT_SoVITS/pretrained_models'), { recursive: true })
  const speech = new SpeechProcess(
    fileURLToPath(new URL('../../python', import.meta.url)),
    roots.cacheRoot,
    async () => ({
      resourceRoot: modelRoot,
      assetsRoot: roots.resourceLibraryRoot,
      pythonExecutable: 'python3',
      precision: 'float32',
      architecture: 'v2',
      device: 'cpu',
      maxConcurrent: 1,
      maxResident: 1
    })
  )
  cleanups.push(() => speech.close())
  const connection = await speech.connect()
  expect(connection.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/)
  expect(connection.runId).toBeTruthy()
  expect(await readdir(roots.cacheRoot)).toEqual(['logs', 'speech-temp'])
  expect(await readdir(path.join(roots.cacheRoot, 'logs'))).toEqual(['speech.log'])
})
