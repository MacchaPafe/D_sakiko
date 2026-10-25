import { mkdtemp, mkdir, writeFile, readFile, rm, rename, symlink, stat } from 'node:fs/promises'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { afterEach, expect, test, vi } from 'vitest'
import { MediaAssets } from '../../src/backend/resources/interface.js'
import { Settings } from '../../src/backend/settings/interface.js'
import { gptSovitsShared } from '../helpers/gpt-sovits.js'
import { SpeechResources } from '../../src/backend/speech/resources.js'
import { ResourceImports } from '../../src/backend/resources/imports.js'
import { CharacterCatalog } from '../../src/backend/characters/interface.js'

const cleanups = []
afterEach(async () => {
  vi.restoreAllMocks()
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function fixture() {
  const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-collection-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const roots = {
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  }
  const assets = new MediaAssets(roots)
  const settings = new Settings({ assets, directory: roots.appDataRoot })
  await assets.initialize()
  const speechResources = new SpeechResources({ assets, settings, cacheRoot: roots.cacheRoot })
  async function resource(purpose = 'chat-image', owner = 'source') {
    return (
      await assets.importAsset(
        {
          kind: 'bytes',
          name: 'test.bin',
          mediaType: 'application/octet-stream',
          bytes: Buffer.from('resource')
        },
        owner,
        purpose
      )
    ).asset
  }
  return { directory, roots, assets, settings, speechResources, resource }
}
function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('预览无副作用，即时清理只删除零引用登记文件，限定候选不会扩大范围', async () => {
  const { assets, resource } = await fixture()
  const unused = await resource()
  const retained = await resource('audio', 'keep')
  const other = await resource('temporary', 'other')
  await assets.releaseOwner('source')
  await assets.releaseOwner('other')
  const file = await assets.localPath(unused)
  const index = await readFile(assets.index, 'utf8')
  expect(
    (await assets.collectUnused({ dryRun: true })).candidates.map((info) => info.asset)
  ).toEqual([unused, other])
  expect(await readFile(assets.index, 'utf8')).toBe(index)
  expect(await readFile(file, 'utf8')).toBe('resource')
  const result = await assets.collectUnused({ resources: [unused, retained] })
  expect(result.deleted).toEqual([unused])
  expect(result.skipped).toEqual([{ asset: retained, reason: 'referenced' }])
  await expect(stat(file)).rejects.toHaveProperty('code', 'ENOENT')
  expect(await assets.inspect(other)).toBeTruthy()
  expect((await assets.collectUnused()).deleted).toEqual([other])
})

test('真实异步读取未结束时即使业务 owner 释放也不会清理', async () => {
  const { assets, resource } = await fixture()
  const ref = await resource()
  const started = deferred()
  const done = deferred()
  const use = assets.using(ref, async () => {
    started.resolve()
    await done.promise
    return assets.read(ref)
  })
  await started.promise
  await assets.releaseOwner('source')
  expect((await assets.collectUnused()).deleted).toEqual([])
  done.resolve()
  expect(Buffer.from(await use).toString()).toBe('resource')
  expect((await assets.collectUnused()).deleted).toEqual([ref])
})

test('删除和索引发布共用队列，删除中新增引用不会复活资源', async () => {
  const { assets, resource } = await fixture()
  const ref = await resource()
  await assets.releaseOwner('source')
  const entered = deferred()
  const resume = deferred()
  const remove = assets.collector.remove.bind(assets.collector)
  vi.spyOn(assets.collector, 'remove').mockImplementation(async (file) => {
    entered.resolve()
    await resume.promise
    await remove(file)
  })
  const collection = assets.collectUnused()
  await entered.promise
  const adding = assets.addReferences('late', [ref])
  const rejected = expect(adding).rejects.toHaveProperty('problem.code', 'asset_missing')
  resume.resolve()
  expect((await collection).deleted).toEqual([ref])
  await rejected
})

test('单文件删除失败继续后续候选，下次调用可重试', async () => {
  const { assets, resource } = await fixture()
  const first = await resource()
  const second = await resource()
  await assets.releaseOwner('source')
  vi.spyOn(assets.collector, 'remove').mockRejectedValueOnce(
    Object.assign(new Error('denied'), { code: 'EACCES' })
  )
  const result = await assets.collectUnused()
  expect(result.aborted).toBe(false)
  expect(result.failed[0].asset).toEqual(first)
  expect(result.deleted).toEqual([second])
  expect((await assets.collectUnused()).deleted).toEqual([first])
})

test('文件删除后索引发布失败会停止并隔离 ID，重启只清除零引用缺失记录', async () => {
  const { assets, roots, resource } = await fixture()
  const first = await resource()
  const next = await resource()
  const retained = await resource('audio', 'keep')
  await rm(await assets.localPath(retained))
  await assets.releaseOwner('source')
  vi.spyOn(assets.collector, 'persist').mockRejectedValueOnce(new Error('index full'))
  const result = await assets.collectUnused()
  expect(result.aborted).toBe(true)
  expect(result.deleted).toEqual([])
  await expect(assets.createTemporaryOwner('use', [first])).rejects.toHaveProperty(
    'problem.code',
    'asset_missing'
  )
  await expect(assets.addReferences('late', [first])).rejects.toHaveProperty(
    'problem.code',
    'asset_missing'
  )
  expect(await assets.read(next)).toBeTruthy()
  const restarted = new MediaAssets(roots)
  await restarted.initialize()
  expect((await restarted.state()).assets[first.id]).toBeUndefined()
  expect(await restarted.inspect(retained)).toBeTruthy()
  expect(await restarted.read(next)).toBeTruthy()
})

test('资源库离线时跳过该根目录，不清空索引，也不妨碍其他根目录清理', async () => {
  const { assets, roots, resource } = await fixture()
  const model = await resource('model')
  const image = await resource()
  await assets.releaseOwner('source')
  await rename(roots.resourceLibraryRoot, roots.resourceLibraryRoot + '-offline')
  const result = await assets.collectUnused()
  expect(result.failed[0].asset).toEqual(model)
  expect(result.deleted).toEqual([image])
  expect(await assets.inspect(model)).toBeTruthy()
})

test('目录移走后清理失败不能重新使用，重启可继续清理，包外文件不受影响', async () => {
  const { directory, assets, roots } = await fixture()
  const source = path.join(directory, 'package')
  await mkdir(source)
  await writeFile(path.join(source, 'model.json'), '{}')
  const ref = (
    await assets.importAsset(
      { kind: 'local-directory', path: source, entry: 'model.json' },
      'source',
      'live2d'
    )
  ).asset
  await assets.releaseOwner('source')
  vi.spyOn(assets.collector, 'remove').mockRejectedValueOnce(new Error('busy'))
  expect((await assets.collectUnused()).failed).toHaveLength(1)
  await expect(assets.read(ref)).rejects.toThrow()
  const restarted = new MediaAssets(roots)
  await restarted.initialize()
  await expect(restarted.addReferences('late', [ref])).rejects.toThrow()
  expect((await restarted.collectUnused()).deleted).toEqual([ref])
  expect(await readFile(path.join(source, 'model.json'), 'utf8')).toBe('{}')
})

test('拒绝沿内部符号链接清理，未知文件不会作为候选', async () => {
  const { directory, assets, resource } = await fixture()
  const ref = await resource()
  const file = await assets.localPath(ref)
  const outside = path.join(directory, 'outside')
  await writeFile(outside, 'keep')
  await rm(file)
  await symlink(outside, file)
  await assets.releaseOwner('source')
  expect((await assets.collectUnused()).failed[0].asset).toEqual(ref)
  expect(await readFile(outside, 'utf8')).toBe('keep')
  expect(await assets.inspect(ref)).toBeTruthy()
})

test('草稿可保护已有资源，过期身份不能续命，显式重建须确保资源尚在', async () => {
  const { assets, resource } = await fixture()
  const imports = new ResourceImports(assets)
  const ref = await resource()
  const { draftId } = await imports.createDraft([ref])
  await assets.releaseOwner('source')
  expect((await assets.collectUnused()).deleted).toEqual([])
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 31 * 60 * 1000)
  await expect(imports.renewDraft(draftId)).rejects.toHaveProperty('problem.code', 'lease_expired')
  const recovery = await imports.createDraft([ref])
  await assets.collectUnused()
  await imports.discardDraft(recovery.draftId)
  expect((await assets.collectUnused()).deleted).toEqual([ref])
  await expect(imports.createDraft([ref])).rejects.toThrow()
})

test('角色读取与运行引用交接期间更换配置必须等待，旧快照继续可用', async () => {
  const { assets, roots, resource } = await fixture()
  const catalog = new CharacterCatalog({ assets, directory: roots.appDataRoot })
  const ref = await resource()
  const card = await catalog.save({
    id: 'character',
    revision: 0,
    defaultFormId: 'main',
    forms: [
      {
        id: 'main',
        displayName: 'test',
        avatar: ref,
        voice: null,
        models: [],
        defaultModelId: null
      }
    ]
  })
  await assets.releaseOwner('source')
  const entered = deferred()
  const resume = deferred()
  const resolve = catalog.resolveCapabilities.bind(catalog)
  vi.spyOn(catalog, 'resolveCapabilities').mockImplementationOnce(async (...args) => {
    const value = await resolve(...args)
    entered.resolve()
    await resume.promise
    return value
  })
  const owner = await assets.createTemporaryOwner('turn')
  const capture = catalog.acquireCapabilities(owner, card.id, {})
  await entered.promise
  const edit = catalog.save({ ...card, forms: [{ ...card.forms[0], avatar: null }] })
  expect((await assets.collectUnused()).deleted).toEqual([])
  resume.resolve()
  expect((await capture).avatar).toEqual(ref)
  await edit
  expect((await assets.collectUnused()).deleted).toEqual([])
  await assets.releaseOwner(owner)
  expect((await assets.collectUnused()).deleted).toEqual([ref])
})

test('显式路径不创建视图，设置切换后仍保护运行文件，退出后只回收独有资源', async () => {
  const { assets, roots, settings, speechResources, resource } = await fixture()
  const shared = await resource('model')
  const v2 = await resource('model')
  const v4 = await resource('model')
  const manifest = {
    shared: gptSovitsShared(shared),
    versions: { v2: { baseSovits: v2 }, v4: { vocoder: v4 } }
  }
  await settings.set('speechStartup.gptSovits.resources', manifest)
  await assets.releaseOwner('source')
  expect((await assets.state()).owners['settings:global']).toHaveLength(3)
  const running = await speechResources.acquire()
  cleanups.push(running.releaseResources)
  await running.bindResources(process.pid)
  expect(running.gptSovits.resources.bert.weights).toBe(await assets.localPath(shared))
  expect(running.gptSovits.resources.baseSovits).toBe(await assets.localPath(v2))
  expect(running.gptSovits.resources.vocoder).toBeUndefined()
  await expect(stat(path.join(roots.cacheRoot, 'speech-resources'))).rejects.toHaveProperty(
    'code',
    'ENOENT'
  )
  await settings.set('speechStartup.gptSovits.resources', {
    shared: manifest.shared,
    versions: { v4: manifest.versions.v4 }
  })
  expect((await assets.collectUnused()).deleted).toEqual([])
  await running.releaseResources(process.pid)
  expect((await assets.collectUnused()).deleted).toEqual([v2])
  expect((await assets.state()).owners['settings:global']).toHaveLength(2)
})

test('未完成的配置可以保存，启动时按用途报错，并拒绝旧格式与非模型引用', async () => {
  const { settings, speechResources, resource } = await fixture()
  await settings.set('display.theme', 'dark')
  await expect(speechResources.acquire()).rejects.toHaveProperty(
    'problem.code',
    'missing_resources'
  )
  const model = await resource('model')
  await settings.set('speechStartup.gptSovits.resources', {
    shared: { bert: { weights: model } },
    versions: {}
  })
  await expect(speechResources.acquire()).rejects.toThrow(/bert.config/)
  const image = await resource()
  await expect(
    settings.set('speechStartup.gptSovits.resources', {
      shared: { bert: { weights: image } },
      versions: {}
    })
  ).rejects.toHaveProperty('problem.code', 'invalid_resource_manifest')
  await expect(
    settings.set('speechStartup.gptSovits.resources', {
      shared: { 'GPT_SoVITS/pretrained_models/w.bin': model },
      versions: {}
    })
  ).rejects.toThrow()
  await expect(
    settings.set('speechStartup.resources', { shared: {}, versions: {} })
  ).rejects.toThrow()
})

test('路径解析失败时释放临时引用，架构专属资源在启动前验证', async () => {
  const { assets, settings, speechResources, resource } = await fixture()
  const model = await resource('model')
  await settings.set('speechStartup.gptSovits.resources', {
    shared: gptSovitsShared(model),
    versions: {}
  })
  vi.spyOn(assets, 'localPath').mockRejectedValueOnce(new Error('文件离线'))
  await expect(speechResources.acquire()).rejects.toThrow('文件离线')
  expect(Object.keys((await assets.state()).temporaryOwners)).toEqual([])
  await settings.set('speechStartup.gptSovits.architecture', 'v4')
  await expect(speechResources.acquire()).rejects.toThrow(/vocoder/)
  await settings.set('speechStartup.gptSovits.architecture', 'v2ProPlus')
  await expect(speechResources.acquire()).rejects.toThrow(/speakerEncoder/)
})

test('库内逐文件登记不依赖 GPT-SoVITS 目录名，仍拒绝越界和符号链接', async () => {
  const { roots, assets } = await fixture()
  const name = 'arbitrary/无后缀'
  const target = path.join(roots.resourceLibraryRoot, 'models', name)
  await mkdir(path.dirname(target), { recursive: true })
  await writeFile(target, 'model')
  const owner = await assets.createTemporaryOwner('registration')
  const refs = await assets.registerModelFiles([name], owner)
  expect(await assets.localPath(refs[name])).toBe(target)
  expect(await assets.registerModelFiles([name], owner)).toEqual(refs)
  await expect(assets.registerModelFiles(['../outside'], owner)).rejects.toThrow()
  await symlink(target, target + '-link')
  await expect(assets.registerModelFiles([name + '-link'], owner)).rejects.toThrow()
})

test('POSIX 仍检查进程组；Windows 风格的主进程引用在进程消失后可恢复释放', async () => {
  const { assets, resource, roots } = await fixture()
  const ref = await resource('model')
  const owner = await assets.createTemporaryOwner('speech-base', [ref])
  await assets.bindOwnerProcess(owner, 12345, 12345)
  await assets.releaseOwner('source')
  vi.spyOn(process, 'kill').mockImplementation((pid) => {
    if (pid === -12345) return true
    throw Object.assign(new Error('gone'), { code: 'ESRCH' })
  })
  const restarted = new MediaAssets(roots)
  await restarted.initialize()
  expect((await restarted.collectUnused()).deleted).toEqual([])
  expect(await restarted.releaseProcessOwner(owner)).toBe(false)
  process.kill.mockImplementation(() => {
    throw Object.assign(new Error('gone'), { code: 'ESRCH' })
  })
  expect((await restarted.collectUnused()).deleted).toEqual([ref])
  const another = await resource('model')
  const windows = await assets.createTemporaryOwner('speech-base', [another])
  await assets.bindOwnerProcess(windows, 54321)
  await assets.releaseOwner('source')
  const nextSession = new MediaAssets(roots)
  await nextSession.initialize()
  expect((await nextSession.state()).temporaryOwners[windows]).toBeUndefined()
  expect((await nextSession.collectUnused()).deleted).toEqual([another])
})

test('隔离目录在离线重启后仍不能交给新 owner，上线后可继续删除', async () => {
  const { directory, roots, assets } = await fixture()
  const source = path.join(directory, 'package')
  await mkdir(source)
  await writeFile(path.join(source, 'model.json'), '{}')
  const ref = (
    await assets.importAsset({ kind: 'local-directory', path: source }, 'source', 'model')
  ).asset
  await assets.releaseOwner('source')
  vi.spyOn(assets.collector, 'remove').mockRejectedValueOnce(new Error('busy'))
  expect((await assets.collectUnused()).failed).toHaveLength(1)
  await rename(roots.resourceLibraryRoot, roots.resourceLibraryRoot + '-offline')
  const restarted = new MediaAssets(roots)
  await restarted.initialize()
  await expect(restarted.addReferences('late', [ref])).rejects.toHaveProperty(
    'problem.code',
    'asset_unavailable'
  )
  await rename(roots.resourceLibraryRoot + '-offline', roots.resourceLibraryRoot)
  await expect(restarted.addReferences('late', [ref])).rejects.toHaveProperty(
    'problem.code',
    'asset_missing'
  )
  await expect(restarted.createTemporaryOwner('use', [ref])).rejects.toThrow()
  expect((await restarted.collectUnused()).deleted).toEqual([ref])
  expect((await restarted.state()).owners.late).toBeUndefined()
})

test('隔离标记优先于失效引用，原父目录缺失也不阻止重试', async () => {
  const { directory, roots, assets } = await fixture()
  const source = path.join(directory, 'package')
  await mkdir(source)
  await writeFile(path.join(source, 'model.json'), '{}')
  const ref = (
    await assets.importAsset({ kind: 'local-directory', path: source }, 'source', 'model')
  ).asset
  await assets.releaseOwner('source')
  vi.spyOn(assets.collector, 'remove').mockRejectedValueOnce(new Error('busy'))
  await assets.collectUnused()
  const state = await assets.state()
  state.owners['stale-owner'] = [ref.id]
  await writeFile(assets.index, JSON.stringify(state))
  await rm(path.join(roots.resourceLibraryRoot, 'models'), { recursive: true })
  const restarted = new MediaAssets(roots)
  await restarted.initialize()
  await expect(restarted.addReferences('late', [ref])).rejects.toThrow()
  expect((await restarted.collectUnused()).deleted).toEqual([ref])
  expect((await restarted.state()).owners['stale-owner']).toEqual([])
})

test('全量清理可回收隔离区的无索引残留，限定清理和预览不扩大范围', async () => {
  const { roots, assets } = await fixture()
  const staged = path.join(roots.resourceLibraryRoot, '.asset-gc', 'orphan')
  await mkdir(staged, { recursive: true })
  await writeFile(path.join(staged, 'partial'), 'partial')
  await assets.collectUnused({ dryRun: true })
  await assets.collectUnused({ resources: [] })
  expect(await stat(staged)).toBeTruthy()
  expect((await assets.collectUnused()).deleted).toContainEqual({ id: 'orphan' })
  await expect(stat(staged)).rejects.toHaveProperty('code', 'ENOENT')
})
