import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = fs.readFileSync(path.join(root, 'src/renderer/statemachine/parameter-adapter.ts'), 'utf8')
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText
const adapterModule = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

const v2Values = [0.25, 0.5]
const v2 = {
  getParamIndex: name => ({ PARAM_MOUTH_OPEN_Y: 0, PARAM_EYE_L_OPEN: 1 }[name] ?? -1),
  getParamFloat: index => v2Values[index],
  setParamFloat: (index, value) => { v2Values[index] = value },
}
const v2Adapter = adapterModule.createParameterAdapter({ internalModel: { coreModel: v2 } })
assert.equal(v2Adapter.get('PARAM_MOUTH_OPEN_Y'), 0.25)
assert.equal(v2Adapter.set('PARAM_MOUTH_OPEN_Y', 0.8), true)
assert.equal(v2Values[0], 0.8)

const v3Values = [0.1, 0.2]
const v3 = {
  getParameterCount: () => v3Values.length,
  getParameterIndex: name => ({
    PARAM_MOUTH_OPEN_Y: 2,
    PARAM_EYE_L_OPEN: 3,
    PARAM_EYE_R_OPEN: 4,
    ParamMouthOpenY: 0,
    ParamEyeLOpen: 1,
    ParamEyeROpen: 1,
  }[name] ?? 5),
  getParameterValueByIndex: index => v3Values[index],
  setParameterValueByIndex: (index, value) => { v3Values[index] = value },
}
const v3Adapter = adapterModule.createParameterAdapter({ internalModel: { coreModel: v3 } })
assert.equal(v3Adapter.get('PARAM_EYE_L_OPEN'), 0.2)
assert.equal(v3Adapter.set('PARAM_MOUTH_OPEN_Y', 0.9), true)
assert.equal(v3Values[0], 0.9)
assert.equal(v3Adapter.index('PARAM_EYE_R_OPEN'), 1)
assert.equal(v3Adapter.set('PARAM_EYE_R_OPEN', 0.75), true)
assert.equal(v3Values[1], 0.75)

console.log('Cubism2/Cubism3-4 parameter adapter checks passed.')
