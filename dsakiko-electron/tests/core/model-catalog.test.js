import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, expect, test } from 'vitest'
import {
  buildModelCatalog,
  resolveModelCatalog
} from '../../src/backend/characters/model-catalog.js'
import { resolvePerformance } from '../../src/backend/performance/selection.js'
import { Performance } from '../../src/backend/performance/interface.js'
import { MediaAssets } from '../../src/backend/resources/interface.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
const data = {
  FileReferences: {
    Motions: {
      happiness: [{ File: 'mtn_nod_C.motion3.json' }],
      talking_motion: [{ File: 'mtn_nod_C.motion3.json' }]
    },
    Expressions: [
      { Name: 'exp_smile01', File: 'smile.exp3.json' },
      { Name: 'exp_idle01', File: 'idle.exp3.json' }
    ]
  },
  DSakiko: {
    MotionAssets: [
      { File: 'mtn_nod_C.motion3.json', FadeInTime: 0.4 },
      { File: 'mtn_nod_L.motion3.json' },
      { File: 'mtn_secret_C.motion3.json' }
    ]
  }
}
async function catalog() {
  return buildModelCatalog(
    data,
    { motions: { mtn_nod: '已有的点头标注' }, expressions: { exp_smile01: '已有的微笑标注' } },
    {},
    async (file) => file
  )
}

test('读取 Python 字符串标注、共享系列、bindings、完整索引和有效组合', async () => {
  const result = await buildModelCatalog(
    data,
    {
      series: 'sample',
      bindings: { 'mtn_secret_C.motion3.json': 'special' },
      motions: { mtn_nod: '已有的点头标注' },
      presets: [
        { motion: 'mtn_nod', expression: 'exp_smile01' },
        { motion: 'missing', expression: 'exp_smile01' }
      ]
    },
    {
      series: {
        sample: { motions: { special: '共享标注' }, expressions: { exp_smile01: '共享微笑' } }
      }
    },
    async (file) => file
  )
  expect(result.version).toBe('v3')
  expect(result.motions.mtn_nod.description).toBe('已有的点头标注')
  expect(result.motions.mtn_nod.variants).toHaveProperty('L')
  expect(result.motions.special.description).toBe('共享标注')
  expect(result.expressions.exp_smile01.description).toBe('共享微笑')
  expect(result.groups.happiness).toEqual(['mtn_nod'])
  expect(result.presets).toHaveLength(1)
  expect(result.motions.mtn_nod.variants.C.entry.FadeInTime).toBe(0.4)
})

test('逻辑 ID 冲突与方向回退；不存在的成员不会成为候选', async () => {
  const result = await buildModelCatalog(
    {
      FileReferences: {
        Motions: {
          happiness: [
            { File: 'a/nod_C.motion3.json' },
            { File: 'b/nod_C.motion3.json' },
            { File: 'missing.motion3.json' }
          ]
        }
      }
    },
    {},
    {},
    async (file) => (file.startsWith('missing') ? null : file)
  )
  expect(Object.keys(result.motions)).toHaveLength(2)
  expect(Object.keys(result.motions).every((id) => /^[a-f0-9]{8}_nod$/u.test(id))).toBe(true)
  const value = await catalog()
  expect(
    resolvePerformance(value, { motion: 'mtn_nod', expression: 'missing' }, 'happiness', {
      direction: 'L'
    })
  ).toMatchObject({
    motion: { member: 'mtn_nod_L.motion3.json' },
    expression: { id: 'exp_smile01' },
    fallbackReasons: ['expression_unavailable']
  })
  expect(
    resolvePerformance(value, { motion: 'missing', expression: 'exp_idle01' }, 'happiness', {
      direction: 'R'
    })
  ).toMatchObject({
    motion: { member: 'mtn_nod_C.motion3.json' },
    expression: { id: 'exp_idle01' },
    fallbackReasons: ['motion_unavailable']
  })
})

test('v2 忽略显式 v3 选择；v3 接受旧 v2 情绪；活动使用动作组', async () => {
  const v2 = await buildModelCatalog(
    {
      motions: { happiness: [{ file: 'smile.mtn' }], talking_motion: [{ file: 'nod.mtn' }] },
      expressions: [{ name: 'smile', file: 'smile.exp.json' }]
    },
    {},
    {},
    async (file) => file
  )
  const selection = { motion: 'mtn_nod', expression: 'exp_smile01' }
  expect(resolvePerformance(v2, selection, 'happiness')).toMatchObject({
    version: 'v2',
    motion: { member: 'smile.mtn' },
    expression: null
  })
  expect(selection).toEqual({ motion: 'mtn_nod', expression: 'exp_smile01' })
  expect(resolvePerformance(v2, selection, null, { activity: 'voice-input' }).motion.member).toBe(
    'nod.mtn'
  )
  const v3 = await catalog()
  expect(resolvePerformance(v3, null, 'label_0')).toMatchObject({
    motion: { id: 'mtn_nod' },
    expression: { id: 'exp_smile01' }
  })
  expect(resolvePerformance(v3, selection, null, { activity: 'voice-input' }).group).toBe(
    'talking_motion'
  )
})

test('实时、换模型和回放使用同一解析规则，不改写原始材料', async () => {
  const v3 = await catalog()
  const v2 = await buildModelCatalog(
    { motions: { happiness: [{ file: 'smile.mtn' }] } },
    {},
    {},
    async (file) => file
  )
  const performance = new Performance({
    resolveModel: async (presentation) => (presentation.model.id === 'v3' ? v3 : v2)
  })
  cleanups.push(() => performance.close())
  const scene = {
    background: null,
    slots: [
      {
        id: 'actor',
        displayName: '角色',
        presentation: { model: { id: 'v3' }, mapping: null },
        layout: { x: 0, y: 0, scale: 1, facing: 'left' }
      }
    ]
  }
  const guide = {
    slotId: 'actor',
    text: 'hello',
    translation: null,
    emotion: 'happiness',
    performance: { motion: 'mtn_nod', expression: 'exp_idle01' },
    audio: null,
    ready: true
  }
  const sequenceId = await performance.createSequence(scene)
  await performance.appendGuides(sequenceId, [guide])
  expect(
    (await performance.getProgress(sequenceId)).current.guide.resolvedPerformance.expression.id
  ).toBe('exp_idle01')
  scene.slots[0].presentation.model.id = 'v2'
  await performance.setScene(sequenceId, scene)
  const live = (await performance.getProgress(sequenceId)).current.guide
  expect(live.resolvedPerformance.expression).toBeNull()
  expect(live.performance).toEqual(guide.performance)
  expect((await performance.resolveGuides(scene, [guide]))[0]).toEqual(live)
  await performance.setActivity(sequenceId, 'thinking')
  expect(
    (await performance.getProgress(sequenceId)).defaultPerformances[0].resolvedPerformance.group
  ).toBe('text_generating')
})

test('真实资源包自动发现相邻及角色共享标注，阻止越界成员', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'model-catalog-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const folder = path.join(directory, 'source')
  await mkdir(path.join(folder, 'live2D_model'), { recursive: true })
  const files = {
    'performance.shared.json': { series: { sample: { expressions: { exp_smile01: '共享微笑' } } } },
    'live2D_model/model.model3.json': {
      ...data,
      DSakiko: {
        MotionAssets: [...data.DSakiko.MotionAssets, { File: '../../outside.motion3.json' }]
      }
    },
    'live2D_model/model.model3.performance.json': {
      series: 'sample',
      motions: { mtn_nod: '原文件标注' }
    },
    'live2D_model/mtn_nod_C.motion3.json': {},
    'live2D_model/mtn_nod_L.motion3.json': {},
    'live2D_model/mtn_secret_C.motion3.json': {},
    'live2D_model/smile.exp3.json': {},
    'live2D_model/idle.exp3.json': {}
  }
  for (const [name, contents] of Object.entries(files))
    await writeFile(path.join(folder, name), JSON.stringify(contents))
  const assets = new MediaAssets({
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  })
  const imported = await assets.importAsset(
    { kind: 'local-directory', path: folder, entry: 'live2D_model/model.model3.json' },
    'source',
    'model'
  )
  const result = await resolveModelCatalog(assets, { model: imported.asset, mapping: null })
  expect(result.motions.mtn_nod.description).toBe('原文件标注')
  expect(result.expressions.exp_smile01.description).toBe('共享微笑')
  expect(result.motions.mtn_nod.variants.C.member).toBe('live2D_model/mtn_nod_C.motion3.json')
  expect(result.motions).not.toHaveProperty('outside')
})
