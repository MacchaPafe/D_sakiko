import path from 'node:path'
import os from 'node:os'
import { mkdtemp, mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises'
import { afterEach, expect, test } from 'vitest'
import { compressBuiltinCharacters } from '../../scripts/resources/pack-builtin-characters.mjs'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { readJson, writeJson } from '../../src/backend/core/support.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'sakiko-builtin-'))
  cleanups.push(() => rm(root, { recursive: true, force: true }))
  const source = path.join(root, 'source')
  const output = path.join(root, 'packed')
  const characters = []
  for (const id of ['sakiko', 'anon', 'soyo']) {
    const forms = []
    for (const formId of id === 'sakiko' ? ['black', 'white'] : ['default']) {
      const directory = 'models/' + id + '-' + formId
      await writeJson(path.join(source, directory, 'model.model3.json'), {
        Version: 3,
        FileReferences: { Moc: 'model.moc3', Textures: [] }
      })
      await writeFile(path.join(source, directory, 'model.moc3'), id + ':' + formId)
      forms.push({ id: formId, directory, entry: 'model.model3.json' })
    }
    characters.push({
      id,
      name: id,
      description: id,
      themeColor: null,
      defaultFormId: forms[0].id,
      previewFormId: forms.at(-1).id,
      forms
    })
  }
  await writeJson(path.join(source, 'manifest.json'), { version: 1, characters })
  const manifest = await compressBuiltinCharacters({ source, output })
  const roots = {
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache')
  }
  async function backend(directory = output) {
    const result = await createCoreBackend({
      ...roots,
      builtinDirectory: directory,
      scanReminders: false
    })
    cleanups.push(() => result.close())
    return result
  }
  return { root, source, output, manifest, roots, backend }
}

test('三个角色包保留四套形态；启动仅释放白祥，重启复用缓存且预览不写角色', async () => {
  const f = await fixture()
  expect((await readdir(f.output)).sort()).toEqual(['archives', 'manifest.json'])
  expect((await readdir(path.join(f.output, 'archives'))).sort()).toEqual([
    'anon.tar.gz',
    'sakiko.tar.gz',
    'soyo.tar.gz'
  ])
  expect(f.manifest.characters[0].defaultFormId).toBe('black')
  let backend = await f.backend()
  const [first, duplicate] = await Promise.all([
    backend.onboarding.prepareStartupPreview(),
    backend.onboarding.prepareStartupPreview()
  ])
  expect(first).toEqual(duplicate)
  expect(first.formId).toBe('white')
  const moc = path.join(first.directory, 'model.moc3')
  expect(await readFile(moc, 'utf8')).toBe('sakiko:white')
  expect(await backend.characters.list()).toEqual([])
  expect(Object.keys((await backend.assets.state()).assets)).toEqual([])
  const cache = path.join(f.roots.cacheRoot, 'builtin-characters', f.manifest.contentVersion)
  expect(await readdir(cache)).toEqual(['sakiko'])
  expect(await readdir(path.join(cache, 'sakiko'))).toEqual(['white'])
  const mtime = (await stat(moc)).mtimeMs
  await backend.close()
  backend = await f.backend()
  expect(await backend.onboarding.prepareStartupPreview()).toEqual(first)
  expect((await stat(moc)).mtimeMs).toBe(mtime)
  const anon = await backend.onboarding.prepareBuiltinPreview('anon')
  expect(await readFile(path.join(anon.directory, 'model.moc3'), 'utf8')).toBe('anon:default')
})

test('初始化从压缩包导入全部模型，预览文件使用期间保留，退出后回收且日常启动不解压', async () => {
  const f = await fixture()
  const backend = await f.backend()
  const preview = await backend.onboarding.prepareStartupPreview()
  await backend.onboarding.begin({ path: 'quick' })
  expect(await backend.characters.list()).toHaveLength(3)
  expect((await backend.characters.get('sakiko')).defaultFormId).toBe('black')
  for (const role of f.manifest.characters) {
    const saved = await backend.characters.get(role.id)
    for (const form of saved.forms) {
      const file = await backend.assets.localPath(form.models[0].presentation.model)
      expect(await readFile(path.join(path.dirname(file), 'model.moc3'), 'utf8')).toBe(
        role.id + ':' + form.id
      )
    }
  }
  const cache = path.join(f.roots.cacheRoot, 'builtin-characters')
  expect(await readdir(path.join(cache, f.manifest.contentVersion, 'sakiko'))).toEqual(['white'])
  await backend.onboarding.finish()
  expect((await stat(path.join(preview.directory, preview.entry))).isFile()).toBe(true)
  await backend.onboarding.begin({ path: 'quick' })
  expect(Object.keys((await backend.assets.state()).assets)).toHaveLength(4)
  await backend.close()
  await expect(stat(cache)).rejects.toMatchObject({ code: 'ENOENT' })
  await rm(f.output, { recursive: true })
  const restarted = await f.backend()
  expect(await restarted.onboarding.prepareStartupPreview()).toBeNull()
})

test('破损归档不启用半成品，修复后可重试；缺入口的缓存和残留暂存目录会重建', async () => {
  const f = await fixture()
  const archive = path.join(f.output, 'archives/sakiko.tar.gz')
  const bytes = await readFile(archive)
  await writeFile(archive, bytes.subarray(0, 40))
  const backend = await f.backend()
  await expect(backend.onboarding.prepareStartupPreview()).rejects.toThrow()
  const root = path.join(
    f.roots.cacheRoot,
    'builtin-characters',
    f.manifest.contentVersion,
    'sakiko'
  )
  expect(await readdir(root)).toEqual([])
  await writeFile(archive, bytes)
  const preview = await backend.onboarding.prepareStartupPreview()
  await rm(path.join(preview.directory, preview.entry))
  await mkdir(path.join(root, 'white.staging'), { recursive: true })
  await writeFile(path.join(root, 'white.staging', 'incomplete'), 'partial')
  expect(await backend.onboarding.prepareStartupPreview()).toEqual(preview)
  expect((await stat(path.join(preview.directory, preview.entry))).isFile()).toBe(true)
  expect(await readdir(root)).toEqual(['white'])
})

test('新的内容版本替换预览缓存，压缩不会改写来源或覆盖现有输出', async () => {
  const f = await fixture()
  const backend = await f.backend()
  const first = await backend.onboarding.prepareStartupPreview()
  await backend.close()
  const sourceMoc = path.join(f.source, 'models/sakiko-white/model.moc3')
  await writeFile(sourceMoc, 'new-white')
  const next = path.join(f.root, 'next')
  const manifest = await compressBuiltinCharacters({ source: f.source, output: next })
  expect(manifest.contentVersion).not.toBe(f.manifest.contentVersion)
  expect(await readJson(path.join(f.source, 'manifest.json'))).not.toHaveProperty('contentVersion')
  await expect(compressBuiltinCharacters({ source: f.source, output: next })).rejects.toThrow(
    '输出目录已存在'
  )
  const restarted = await f.backend(next)
  const preview = await restarted.onboarding.prepareStartupPreview()
  expect(await readFile(path.join(preview.directory, 'model.moc3'), 'utf8')).toBe('new-white')
  await expect(stat(first.directory)).rejects.toMatchObject({ code: 'ENOENT' })
})
