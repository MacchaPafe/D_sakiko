import { expect, test } from 'vitest'
import {
  catalogTextSaveDelay,
  nextCatalogSaveSchedule
} from '../../src/renderer/src/hooks/use-catalog-editor.js'

test('文字编辑停顿五秒后保存，新输入重新计算停顿时间', () => {
  const first = nextCatalogSaveSchedule(null, catalogTextSaveDelay, 100)
  expect(first).toEqual({ startedAt: 100, saveAt: 5_100 })
  expect(nextCatalogSaveSchedule(first, catalogTextSaveDelay, 4_000)).toEqual({
    startedAt: 100,
    saveAt: 9_000
  })
})

test('连续输入时从本批第一次编辑起最多等待三十秒', () => {
  let schedule = nextCatalogSaveSchedule(null, catalogTextSaveDelay, 100)
  for (let time = 4_100; time < 30_100; time += 4_000)
    schedule = nextCatalogSaveSchedule(schedule, catalogTextSaveDelay, time)

  expect(nextCatalogSaveSchedule(schedule, catalogTextSaveDelay, 29_900)).toEqual({
    startedAt: 100,
    saveAt: 30_100
  })
  expect(nextCatalogSaveSchedule(schedule, catalogTextSaveDelay, 30_200).saveAt).toBe(30_100)
  expect(nextCatalogSaveSchedule(null, catalogTextSaveDelay, 31_000).saveAt).toBe(36_000)
})

test('离散设置立即安排保存，随后输入不会推迟已经到期的保存', () => {
  const typing = nextCatalogSaveSchedule(null, catalogTextSaveDelay, 100)
  const setting = nextCatalogSaveSchedule(typing, 0, 300)
  expect(setting.saveAt).toBe(300)
  expect(nextCatalogSaveSchedule(setting, catalogTextSaveDelay, 400).saveAt).toBe(300)
})
