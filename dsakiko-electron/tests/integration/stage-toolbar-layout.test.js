import { expect, test } from 'vitest'
import {
  placeToolbar,
  placeWardrobe
} from '../../src/renderer/src/features/home/stage-toolbar-layout.js'

const stage = { left: 0, top: 32, width: 744, height: 680 }

const sizes = {
  horizontal: { width: 288, height: 48, gripX: 24, gripY: 24 },
  vertical: { width: 48, height: 281, gripX: 24, gripY: 16 }
}

test('贴边后再向受限方向移动 100 像素才转向，空旷处保留朝向', () => {
  const x = 100
  const y = 507
  const bottomEdge = placeToolbar({ x, y: 407, orientation: 'vertical' }, stage, sizes)
  expect(bottomEdge).toMatchObject({ x, y: 407, orientation: 'vertical' })
  expect(placeToolbar({ ...bottomEdge, y: y - 1 }, stage, sizes)).toEqual(bottomEdge)
  const row = placeToolbar({ x, y, orientation: 'vertical' }, stage, sizes)
  expect(row).toMatchObject({ x, y, orientation: 'horizontal' })
  const higher = placeToolbar({ ...row, y: 250 }, stage, sizes)
  expect(higher).toMatchObject({ y: 250, orientation: 'horizontal' })
  const lower = placeToolbar({ ...higher, y: 315 }, stage, sizes)
  expect(lower).toMatchObject({ y: 315, orientation: 'horizontal' })
  const nearRight = placeToolbar({ ...lower, x: 472 }, stage, sizes)
  expect(nearRight).toMatchObject({ x: 472, y: 315, orientation: 'horizontal' })
  expect(placeToolbar({ ...nearRight, x: 571 }, stage, sizes)).toEqual(nearRight)
  const column = placeToolbar({ ...nearRight, x: 572 }, stage, sizes)
  expect(column).toMatchObject({ x: 572, y: 315, orientation: 'vertical' })
  const corner = placeToolbar({ ...column, x: 373, y }, stage, sizes)
  expect(corner.orientation).toBe('vertical')
  const clear = placeToolbar({ ...corner, x: 372, y }, stage, sizes)
  expect(clear.orientation).toBe('horizontal')
})

test('工具栏可以拖过舞台下方，抵达舞台底边后限位', () => {
  const target = { x: 300, y: 600, orientation: 'horizontal' }
  expect(placeToolbar(target, stage, sizes)).toMatchObject({ x: 300, y: 600 })
  const preview = placeToolbar({ ...target, y: 710 }, stage, sizes)
  expect(preview).toMatchObject({ top: 624, y: 648, orientation: 'horizontal' })
  expect(placeToolbar(preview, stage, sizes)).toEqual(preview)
})

test('服装面板随右侧工具栏向左，随底部工具栏向上，定位只受舞台边界影响', () => {
  const rightBar = { left: 688, top: 350, width: 48, height: 242 }
  const bottomBar = { left: 20, top: 454, width: 242, height: 48 }
  const trigger = { left: 692, top: 515, width: 40, height: 40 }
  const size = { width: 272, height: 200 }
  const beside = placeWardrobe(stage, rightBar, trigger, size, false)
  expect(beside.left + size.width).toBeLessThan(rightBar.left)
  expect(beside.top + size.height).toBe(stage.height - 8)
  const above = placeWardrobe(stage, bottomBar, trigger, size, true)
  expect(above.left).toBe(bottomBar.left)
  expect(above.top + size.height).toBeLessThan(bottomBar.top)
})
