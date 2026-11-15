import { expect, test } from 'vitest'
import {
  StageLayouts,
  defaultStageLayout
} from '../../src/renderer/src/performance/stage-layout.js'

test('V2/V3 默认构图区分，具体模型独立保留缩放和平移，重置不影响其他模型', () => {
  const layouts = new StageLayouts()
  expect(layouts.get('v2-a', 'v2').scale).toBeLessThan(layouts.get('v3-b', 'v3').scale)
  layouts.set('v2-a', { scale: 0.8, offsetX: 120, offsetY: -50 })
  layouts.set('v3-b', { scale: 2, offsetX: -80, offsetY: 100 })
  expect(layouts.get('v2-c', 'v2')).toEqual(defaultStageLayout('v2'))
  expect(layouts.get('v2-a', 'v2')).toEqual({ scale: 0.8, offsetX: 120, offsetY: -50 })
  expect(layouts.reset('v3-b', 'v3')).toEqual(defaultStageLayout('v3'))
  expect(layouts.get('v2-a', 'v2').offsetX).toBe(120)
  layouts.get('v2-a', 'v2').offsetX = 0
  expect(layouts.get('v2-a', 'v2').offsetX).toBe(120)
})
