import { afterEach, expect, test, vi } from 'vitest'
import { analyse } from 'chardet'
import { decodeText } from '../../src/backend/tools/text-file.js'

vi.mock('chardet', () => ({ analyse: vi.fn() }))
afterEach(() => vi.resetAllMocks())

function ambiguous(data, candidates) {
  vi.mocked(analyse).mockReturnValue(candidates)
  try {
    decodeText(data)
    throw new Error('预期编码不确定')
  } catch (error) {
    expect(error.problem?.code).toBe('ambiguous_text_encoding')
    return error.problem
  }
}

test('同分候选优先展示中文，最多三组且仍要求显式重试', () => {
  const result = ambiguous(Buffer.from([0xd6, 0xd0, 0xce, 0xc4]), [
    { name: 'Big5', confidence: 10 },
    { name: 'EUC-JP', confidence: 10 },
    { name: 'EUC-KR', confidence: 10 },
    { name: 'GB18030', confidence: 10 },
    { name: 'windows-1252', confidence: 0 }
  ])
  expect(result.candidates).toHaveLength(3)
  expect(result.candidates[0]).toMatchObject({
    encoding: 'gb18030',
    confidence: 10,
    preview: '中文'
  })
  expect(result.message).toContain('重新调用')
  expect(result.message).toContain('不必强行选择')
})

test('相同完整文本合并，不因相同预览而错误合并不同文本', () => {
  const candidates = [
    { name: 'windows-1252', confidence: 30 },
    { name: 'windows-1250', confidence: 20 }
  ]
  const equivalent = ambiguous(Buffer.from([0xe9]), candidates)
  expect(equivalent.candidates).toEqual([
    {
      encoding: 'windows-1252',
      confidence: 30,
      preview: 'é',
      alternatives: [{ encoding: 'windows-1250', confidence: 20 }]
    }
  ])
  const different = ambiguous(
    Buffer.concat([Buffer.from([0xe9]), Buffer.from('a'.repeat(400)), Buffer.from([0x9f])]),
    candidates
  )
  expect(different.candidates).toHaveLength(2)
  expect(different.candidates[0].preview).toBe(different.candidates[1].preview)
  expect(different.candidates.every((item) => item.alternatives.length === 0)).toBe(true)
})

test('优先非 ASCII 片段，每组最多 200 码点且不切断代理对', () => {
  const data = Buffer.from('a'.repeat(1000) + '😊'.repeat(300), 'utf16le')
  const { candidates } = ambiguous(data, [{ name: 'UTF-16LE', confidence: 50 }])
  expect(candidates[0].preview).toBe('a'.repeat(40) + '😊'.repeat(160))
  expect(Array.from(candidates[0].preview)).toHaveLength(200)
  expect(candidates[0].preview.isWellFormed()).toBe(true)
  expect(
    candidates.reduce((total, item) => total + Array.from(item.preview).length, 0)
  ).toBeLessThanOrEqual(600)
})

test('不支持的编码、错误序列和只有尾部损坏的文件都不生成预览', () => {
  const data = Buffer.concat([Buffer.from('正文'.repeat(300), 'utf16le'), Buffer.from([0xff])])
  const result = ambiguous(data, [
    { name: 'UTF-32LE', confidence: 50 },
    { name: 'UTF-16LE', confidence: 40 },
    { name: 'UTF-8', confidence: 10 }
  ])
  expect(result.candidates).toEqual([])
})
