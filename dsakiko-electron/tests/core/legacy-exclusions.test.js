import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
  realpath,
  symlink,
  stat,
  utimes
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, test } from 'vitest'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { scanLegacyDirectory, validateLegacySource } from '../../src/backend/setup/legacy.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function put(root, name, text) {
  const file = path.join(root, name)
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, text)
  return file
}
async function fixture(layout = {}) {
  const temporary = await mkdtemp(path.join(tmpdir(), 'legacy-exclusions-'))
  cleanups.push(() => rm(temporary, { recursive: true, force: true }))
  const root = await realpath(temporary)
  await put(root, 'live2d_related/anon/name.txt', '爱音')
  await put(root, 'live2d_related/anon/character_description.txt', '保留的旧人设')
  await put(root, 'live2d_related/anon/model/model.model3.json', '{}')
  await put(root, 'live2d_related/anon/model/model.moc3', 'old-model')
  await put(root, 'GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth', 'old-weights')
  const roots = {
    appDataRoot: path.join(root, layout.app ?? 'dsakiko-electron/.local/core-v1'),
    resourceLibraryRoot: path.join(root, layout.library ?? 'dsakiko-electron/.local/library'),
    cacheRoot: path.join(root, layout.cache ?? 'dsakiko-electron/.local/cache')
  }
  const backend = await createCoreBackend({ ...roots, scanReminders: false })
  cleanups.push(() => backend.close())
  return { root, roots, backend }
}
async function scan(backend, root) {
  return backend.setup.scanLegacy((await backend.setup.grantLegacySource(root)).sourceId)
}
async function finish(backend, id) {
  await backend.setup.start(id)
  await expect.poll(async () => (await backend.setup.getJob(id)).status).toBe('succeeded')
}

test('项目根目录包含新版开发数据时可正常导入，写入的新版数据不会成为旧资料', async () => {
  const { root, roots, backend } = await fixture()
  await put(roots.appDataRoot, 'private.txt', 'new-data-must-not-be-read')
  const first = await scan(backend, root)
  expect(first).toMatchObject({
    canImport: true,
    characters: [{ id: 'anon' }],
    gsvFileCount: 1,
    warnings: []
  })
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: first.scanId })
  await finish(backend, plan.id)
  expect((await backend.characters.get('anon')).forms[0].description).toBe('保留的旧人设')
  expect(await readFile(path.join(root, 'live2d_related/anon/model/model.moc3'), 'utf8')).toBe(
    'old-model'
  )
  const second = await scan(backend, root)
  expect(second.characters).toEqual(first.characters)
  expect(second.logicalBytes).toBe(first.logicalBytes)
  expect(second.gsvFileCount).toBe(1)
  expect(second.warnings).toEqual([])
})

test('递归扫描排除新版完整根目录，头像和聊天直接引用也不能绕过；冲突模型整包跳过', async () => {
  const { root, roots, backend } = await fixture({
    app: 'live2d_related/anon/model/new-app',
    library: 'GPT_SoVITS/pretrained_models/new-library',
    cache: 'reference_audio/anon/new-cache'
  })
  const secret = await put(roots.appDataRoot, 'private.txt', 'private-payload')
  await put(roots.appDataRoot, 'fake.model3.json', '{}')
  await put(roots.resourceLibraryRoot, 'new.pth', 'must-not-import')
  const avatar = await put(roots.cacheRoot, 'new.png', 'must-not-import')
  await put(root, 'live2d_related/anon/other/model.model3.json', '{}')
  await put(
    root,
    'd_sakiko_config.json',
    JSON.stringify({ webui_setting: { character_avatars: { anon: avatar } } })
  )
  await put(
    root,
    'reference_audio/all_conversation.json',
    JSON.stringify({
      chat_list: [
        {
          chat_id: 'old-chat',
          name: '旧聊天',
          type: 1,
          message_list: [
            { character_name: 'User', text: '保留正文', attachments: [{ local_path: secret }] }
          ]
        }
      ]
    })
  )
  const snapshot = await scan(backend, root)
  expect(snapshot.characters[0].models).toBe(1)
  expect(snapshot.gsvFileCount).toBe(1)
  expect(snapshot.warnings.some((warning) => warning.code === 'legacy_package_overlaps_data')).toBe(
    true
  )
  expect(snapshot.warnings.some((warning) => warning.code === 'legacy_new_data_excluded')).toBe(
    true
  )
  const internal = JSON.parse(
    await readFile(path.join(roots.appDataRoot, 'setup/scans', snapshot.scanId + '.json'), 'utf8')
  )
  expect(
    Object.values(internal.files).some((file) => /new-app|new-library|new-cache/.test(file.name))
  ).toBe(false)
  expect(internal.media[secret]).toBeNull()
  expect(internal.roles[0].avatar).toBeNull()
  expect(JSON.stringify(internal)).not.toContain('private-payload')
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: snapshot.scanId })
  await put(roots.cacheRoot, 'changed-after-scan.txt', 'new-write')
  await finish(backend, plan.id)
  expect((await backend.characters.get('anon')).forms[0].models).toHaveLength(1)
  expect((await scan(backend, root)).gsvFileCount).toBe(1)
})

test('仍拒绝选择新版根目录或其内部；符号链接别名不能绕过', async () => {
  const { root, roots, backend } = await fixture()
  for (const directory of Object.values(roots)) {
    await mkdir(path.join(directory, 'nested'), { recursive: true })
    await expect(backend.setup.grantLegacySource(directory)).rejects.toHaveProperty(
      'problem.code',
      'legacy_directory_overlaps_data'
    )
    await expect(
      backend.setup.grantLegacySource(path.join(directory, 'nested'))
    ).rejects.toHaveProperty('problem.code', 'legacy_directory_overlaps_data')
  }
  const alias = path.join(root, 'alias')
  await symlink(roots.resourceLibraryRoot, alias)
  await expect(backend.setup.grantLegacySource(alias)).rejects.toHaveProperty(
    'problem.code',
    'legacy_directory_overlaps_data'
  )
})

test('复核与最终整包读取都拒绝新增重叠；历史排除规则跨恢复保留', async () => {
  const { root, roots, backend } = await fixture()
  const snapshot = await scan(backend, root)
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: snapshot.scanId })
  const job = await backend.setup.readJob(plan.id)
  const model = Object.entries(job.files).find(([, file]) => file.entry)
  const nested = path.join(root, 'live2d_related/anon/model/.new-data')
  await mkdir(nested)
  // 模拟恢复时改配的新数据位置：复核和最终复制分别检查，而非只信任先前扫描。
  await expect(validateLegacySource(job.scan, [nested])).rejects.toHaveProperty(
    'problem.code',
    'legacy_package_overlaps_data'
  )
  backend.setup.legacyExcludedRoots.push(nested)
  await expect(
    backend.setup.importFile(job, model[0], new AbortController().signal)
  ).rejects.toHaveProperty('problem.code', 'legacy_package_overlaps_data')
  expect(await backend.characters.list()).toEqual([])
  const persisted = await scanLegacyDirectory(root, Object.values(roots))
  expect(persisted.excludedRoots).toContain(roots.appDataRoot)
  await put(roots.appDataRoot, 'new-file', 'ignored')
  await validateLegacySource(persisted)
})

test('只导入公共清单及角色引用，微小角色权重也硬链接，配置词典和音频独立复制', async () => {
  const { root, roots, backend } = await fixture()
  const entries = {
    'GPT_SoVITS/pretrained_models/chinese-hubert-base/config.json': '{}',
    'GPT_SoVITS/text/G2PWModel/g2pW.onnx': 'shared-onnx',
    'GPT_SoVITS/open_jtalk_dic_utf_8-1.11/matrix.bin': 'dictionary-not-weights',
    'reference_audio/anon/GPT-SoVITS_models/voice.ckpt': 'character-gpt',
    'reference_audio/anon/GPT-SoVITS_models/voice.pth': 'character-sovits',
    'reference_audio/anon/default_ref_audio.txt': '../reference_audio/anon/default.wav',
    'reference_audio/anon/reference_text.txt': '默认参考',
    'reference_audio/anon/reference_audio_language.txt': 'ja',
    'reference_audio/anon/default.wav': 'default-audio',
    'reference_audio/anon/reference_audio_and_text.json': JSON.stringify({
      sadness: { audio_path: '../reference_audio/shared/sad.wav', text: '悲伤参考' }
    }),
    'reference_audio/shared/sad.wav': 'emotion-audio'
  }
  const unwanted = [
    'GPT_SoVITS/pretrained_models/multilingual-e5-small/model.safetensors',
    'GPT_SoVITS/pretrained_models/faster-whisper-small/model.bin',
    'GPT_SoVITS/pretrained_models/unknown.pth',
    'GPT_SoVITS/text/G2PWModel/training-notes.txt',
    'reference_audio/anon/GPT-SoVITS_models/z-unused.ckpt',
    'reference_audio/anon/GPT-SoVITS_models/z-unused.pth',
    'reference_audio/anon/training/unused.wav'
  ]
  for (const [name, content] of Object.entries(entries)) await put(root, name, content)
  for (const name of unwanted) {
    const file = await put(root, name, 'must-not-import')
    await utimes(file, 1, 1)
  }
  const snapshot = await scan(backend, root)
  expect(snapshot.gsvFileCount).toBe(4)
  expect(snapshot.warnings).toEqual([])
  const scanned = await scanLegacyDirectory(root, Object.values(roots))
  await put(root, 'reference_audio/anon/reference_audio_language.txt', '中文')
  await expect(validateLegacySource(scanned)).rejects.toHaveProperty(
    'problem.code',
    'legacy_source_changed'
  )
  const refreshed = await scan(backend, root)
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: refreshed.scanId })
  const job = await backend.setup.readJob(plan.id)
  const selected = Object.values(job.files).map((file) => file.name)
  for (const name of unwanted) expect(selected).not.toContain(name)
  expect(selected).not.toContain('reference_audio/anon/reference_text.txt')
  expect(selected).toContain('reference_audio/shared/sad.wav')
  expect(plan.transfer.hardlinkFiles).toBe(4)
  await finish(backend, plan.id)
  const completed = await backend.setup.readJob(plan.id)
  for (const [key, file] of Object.entries(completed.files)) {
    if (file.entry) continue
    const source = await stat(path.join(root, file.name))
    const destination = await stat(await backend.assets.localPath(completed.refs[key]))
    const linked = source.dev === destination.dev && source.ino === destination.ino
    expect(linked, file.name).toBe(file.mode === 'hardlink')
  }
  const voice = (await backend.characters.get('anon')).forms[0].voice
  expect(voice.defaultReference.language).toBe('all_zh')
  expect(await readFile(await backend.assets.localPath(voice.defaultReference.audio), 'utf8')).toBe(
    'emotion-audio'
  )
  // 删除新版链接只减少目录引用，旧版权重与内容仍然存在。
  const source = path.join(root, 'reference_audio/anon/GPT-SoVITS_models/voice.ckpt')
  const destination = await backend.assets.localPath(voice.gptWeights)
  expect(destination.startsWith(roots.resourceLibraryRoot + path.sep)).toBe(true)
  await rm(destination)
  expect(await readFile(source, 'utf8')).toBe('character-gpt')
})

test('按 Python 的参考语言编号、权重修改时间和手动指定的形态音频导入', async () => {
  const { root, backend } = await fixture()
  const entries = {
    'd_sakiko_config.json': JSON.stringify({
      character_setting: {
        user_characters: [
          null,
          {
            persona_id: 'linked',
            user_as_character_folder_name: 'old-id',
            user_as_character_name: '祥子'
          }
        ]
      }
    }),
    'live2d_related/sakiko/name.txt': '祥子',
    'live2d_related/sakiko/character_description.txt': '测试角色',
    'live2d_related/sakiko/profile.png': '自定义头像',
    'reference_audio/sakiko/GPT-SoVITS_models/a.ckpt': '旧权重',
    'reference_audio/sakiko/GPT-SoVITS_models/z.ckpt': '新权重',
    'reference_audio/sakiko/GPT-SoVITS_models/voice.pth': 'SoVITS',
    'reference_audio/sakiko/reference_audio_language.txt': '# 1 中文 3 日文\r\n3\r\n',
    'reference_audio/sakiko/default_ref_audio_black.txt': '../reference_audio/sakiko/custom.wav',
    'reference_audio/sakiko/reference_text_black_sakiko.txt': '自定义黑祥参考',
    'reference_audio/sakiko/reference_text_white_sakiko.txt': '默认白祥参考',
    'reference_audio/sakiko/custom.wav': '自定义音频',
    'reference_audio/sakiko/black_sakiko.wav': '默认黑祥音频',
    'reference_audio/sakiko/white_sakiko.wav': '默认白祥音频',
    'reference_audio/anon/GPT-SoVITS_models/voice.ckpt': 'GPT',
    'reference_audio/anon/GPT-SoVITS_models/voice.pth': 'SoVITS',
    'reference_audio/anon/reference_audio_language.txt': '2',
    'reference_audio/anon/reference_audio_and_text.json': JSON.stringify({
      emotion_refs: { LABEL_1: { audio_path: '../reference_audio/anon/sad.wav', text: '英语参考' } }
    }),
    'reference_audio/anon/sad.wav': '情绪音频'
  }
  for (const [name, content] of Object.entries(entries)) await put(root, name, content)
  await utimes(path.join(root, 'reference_audio/sakiko/GPT-SoVITS_models/a.ckpt'), 1, 1)
  const snapshot = await scan(backend, root)
  expect(snapshot.warnings).toEqual([])
  expect(snapshot.personaCount).toBe(1)
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: snapshot.scanId })
  await finish(backend, plan.id)
  const { forms } = await backend.characters.get('sakiko')
  const readAsset = async (ref) => readFile(await backend.assets.localPath(ref), 'utf8')
  expect(forms.map(({ voice }) => voice.defaultReference.language)).toEqual(['all_ja', 'all_ja'])
  expect(await readAsset(forms[0].avatar)).toBe('自定义头像')
  expect(await readAsset(forms[0].voice.gptWeights)).toBe('新权重')
  expect(await readAsset(forms[0].voice.defaultReference.audio)).toBe('自定义音频')
  expect(await readAsset(forms[1].voice.defaultReference.audio)).toBe('默认白祥音频')
  const { voice } = (await backend.characters.get('anon')).forms[0]
  expect(voice.emotions.sadness.language).toBe('en')
  expect(await readAsset(voice.emotions.sadness.audio)).toBe('情绪音频')
  const [persona] = await backend.characters.listPersonas()
  expect((await backend.characters.getPersona(persona.id)).source).toEqual({
    kind: 'character',
    characterId: 'sakiko',
    formId: 'black'
  })
})

test('清单显式声明只读权重，不用后缀判断，未列入的同目录文件不扫描', async () => {
  const { root } = await fixture()
  await put(root, 'GPT_SoVITS/pretrained_models/supported/model.safetensors', 'weights')
  await put(root, 'GPT_SoVITS/pretrained_models/supported/dictionary.bin', 'dictionary')
  const result = await scanLegacyDirectory(
    root,
    [],
    [
      { path: 'pretrained_models/supported/model.safetensors', readOnlyWeight: true },
      { path: 'pretrained_models/supported/dictionary.bin' }
    ]
  )
  const files = Object.values(result.gsv).map((key) => result.files[key])
  expect(files).toHaveLength(2)
  expect(files.find((file) => file.name.endsWith('.safetensors')).hardlink).toBe(true)
  expect(files.find((file) => file.name.endsWith('.bin')).hardlink).toBe(false)
})
