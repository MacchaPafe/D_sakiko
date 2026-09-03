import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = fs.readFileSync(path.join(root, 'src/renderer/statemachine/presentation-policy.ts'), 'utf8')
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText
const policy = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

const sakikoV2FixturePath = path.join(root, 'test/fixtures/sakiko-costume-v2.model.json')
assert.ok(fs.existsSync(sakikoV2FixturePath), 'portable Sakiko V2 manifest fixture must be available')
const realSakikoV2 = policy.parseLive2DPresentationMetadata(JSON.parse(fs.readFileSync(sakikoV2FixturePath, 'utf8')))
assert.equal(realSakikoV2.runtimeKind, 'v2', 'FileReferences, not root version, determines the runtime family')
assert.deepEqual(realSakikoV2.expressionIds, ['serious', 'idle'])
for (const [stage, group] of [
  ['cold start', null],
  ['idle_motion', 'idle_motion'],
  ['25s IDLE', 'IDLE'],
  ['emotion', 'happiness'],
  ['idle recovery', 'idle_motion'],
]) {
  const selected = group === null
    ? policy.selectBaseExpression(realSakikoV2, 'serious')
    : policy.selectExpressionForMotion(realSakikoV2, group, 0, 'serious')
  assert.deepEqual(
    selected,
    { expression: 'serious', source: 'base' },
    `black Sakiko must retain serious through ${stage}`,
  )
}
assert.deepEqual(
  policy.selectExpressionForMotion(realSakikoV2, 'happiness', 0, 'idle'),
  { expression: 'idle', source: 'base' },
  'white Sakiko must retain idle through V2 emotion motions',
)
assert.deepEqual(
  policy.selectBaseExpression(realSakikoV2, 'idle'),
  { expression: 'idle', source: 'base' },
  'non-Sakiko V2 models keep their idle base',
)

const v2 = policy.parseLive2DPresentationMetadata({
  version: '3.1',
  motions: {
    happiness: [{ file: 'motions/motion_smile01.mtn' }],
    IDLE: [{ file: 'motions/idle_01.mtn' }],
  },
  expressions: [
    { name: 'idle', file: 'expressions/idle.exp.json' },
    { name: 'serious', file: 'expressions/serious.exp.json' },
    { name: 'exp_smile01', file: 'expressions/smile.exp.json' },
  ],
})
assert.deepEqual(v2.motionFilesByGroup.happiness, ['motions/motion_smile01.mtn'])
assert.deepEqual(v2.expressionIds, ['idle', 'serious', 'exp_smile01'])
assert.equal(v2.runtimeKind, 'v2', 'a V2 root version must not select V3 presentation behavior')
assert.deepEqual(
  policy.selectExpressionForMotion(v2, 'happiness', 0, 'serious'),
  { expression: 'serious', source: 'base' },
  'V2 must retain its role base instead of applying V3 per-motion expressions',
)

const v3 = policy.parseLive2DPresentationMetadata({
  FileReferences: {
    Motions: {
      text_generating: [{ File: 'motions/think_check_01.motion3.json' }],
      text_generating_C: [{ File: 'motions/mtn_thinking01_C.motion3.json' }],
    },
    Expressions: [
      { Name: 'exp_thinking01', File: 'expressions/think.exp3.json' },
      { Name: 'exp_idle01', File: 'expressions/idle.exp3.json' },
    ],
  },
})
assert.deepEqual(v3.motionFilesByGroup.text_generating, ['motions/think_check_01.motion3.json'])
assert.deepEqual(v3.expressionIds, ['exp_thinking01', 'exp_idle01'])
assert.equal(v3.runtimeKind, 'v3')
assert.deepEqual(
  policy.selectExpressionForMotion(v3, 'text_generating', 0, 'idle'),
  { expression: 'exp_thinking01', source: 'motion' },
  'V3 thinking must use the matching expression for its concrete motion file',
)
assert.deepEqual(
  policy.selectExpressionForMotion(v3, 'text_generating_C', 0, 'idle'),
  { expression: 'exp_thinking01', source: 'motion' },
  'V3 center-position groups keep the same upstream filename expression policy',
)
const v3PathExpressions = policy.parseLive2DPresentationMetadata({
  FileReferences: {
    Expressions: ['expressions/exp_idle01.exp3.json'],
  },
})
assert.deepEqual(
  v3PathExpressions.expressionIds,
  ['exp_idle01'],
  'manifest path-only expression entries must remain usable capability ids',
)
assert.deepEqual(
  policy.selectBaseExpression(v2, 'serious'),
  { expression: 'serious', source: 'base' },
  'black Sakiko keeps the serious base when no active cue owns expression',
)
assert.equal(policy.baseSemanticForModel('sakiko', 'black'), 'serious')
assert.equal(policy.baseSemanticForModel('sakiko', 'white'), 'idle')
assert.equal(policy.baseSemanticForModel('anon', 'black'), 'idle')
assert.equal(policy.baseSemanticForModel('anon', undefined, 'serious'), 'serious')
assert.equal(policy.baseSemanticForModel('sakiko', 'white', 'serious'), 'idle')
assert.deepEqual(policy.nextSakikoMaskTransition(true), {
  requestedGroup: 'change_character_maskoff',
  maskOn: false,
})
assert.deepEqual(policy.nextSakikoMaskTransition(false), {
  requestedGroup: 'maskon',
  maskOn: true,
})
assert.deepEqual(policy.selectBlackSakikoEntry(undefined, () => 0.1), {
  requestedGroup: 'change_character',
  maskOn: true,
})
assert.deepEqual(policy.selectBlackSakikoEntry(undefined, () => 0.5), {
  requestedGroup: 'change_character_maskoff',
  maskOn: false,
})
assert.deepEqual(
  policy.nextSakikoMaskTransition(policy.selectBlackSakikoEntry(undefined, () => 0.1).maskOn),
  { requestedGroup: 'change_character_maskoff', maskOn: false },
  'masked black entry must toggle to mask-off next',
)
assert.deepEqual(
  policy.nextSakikoMaskTransition(policy.selectBlackSakikoEntry(undefined, () => 0.5).maskOn),
  { requestedGroup: 'maskon', maskOn: true },
  'mask-off black entry must toggle to mask-on next',
)

const noEmotionMotion = {
  runtimeKind: 'v3',
  motionFilesByGroup: {},
  expressionIds: ['exp_thinking01', 'serious', 'idle'],
}
assert.deepEqual(
  policy.selectExpressionForMotion(noEmotionMotion, 'text_generating', 0, 'serious'),
  { expression: 'exp_thinking01', source: 'semantic' },
  'missing thinking motion must still replace a stale expression semantically',
)
assert.deepEqual(
  policy.selectExpressionForMotion(noEmotionMotion, 'anger', 0, 'serious'),
  { expression: 'idle', source: 'idle' },
  'missing emotion motion must use upstream idle fallback instead of the current base',
)
assert.deepEqual(
  policy.selectBaseExpression({ runtimeKind: 'v3', motionFilesByGroup: {}, expressionIds: [] }, 'idle'),
  { expression: null, source: 'none' },
  'V3 without a supported base expression must not request a reset lifecycle',
)

console.log('Live2D V2/V3 manifest and local presentation policy checks passed.')
