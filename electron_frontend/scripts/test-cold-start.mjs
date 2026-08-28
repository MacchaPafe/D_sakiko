import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const app = fs.readFileSync(path.join(root, 'src/renderer/App.vue'), 'utf8')
const stage = fs.readFileSync(path.join(root, 'src/renderer/components/Live2DStage.vue'), 'utf8')

assert.match(app, /<Live2DStage\s+:key=/, 'App must mount Live2DStage during cold start')
assert.doesNotMatch(app, /<Live2DStage[^>]*v-if="customModelPath"/, 'Stage must not depend on load_model for existence')
assert.match(stage, /if \(!props\.modelPath\)/, 'Stage must wait for an authoritative model command')
assert.doesNotMatch(stage, /live2d\/sakiko\/live2D_model\/3\.model\.json/, 'Stage must not select Sakiko as a business fallback')
assert.match(app, /renderer_hello/, 'Electron must announce itself before a model exists')

console.log('Electron cold-start bootstrap checks passed.')
