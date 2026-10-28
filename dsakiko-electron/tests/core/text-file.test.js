import { mkdtemp, realpath, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, expect, test } from 'vitest'
import { decodeText } from '../../src/backend/tools/text-file.js'
import { fileTools } from '../../src/backend/tools/files.js'

const directories = []
afterEach(async () => {
  for (const directory of directories.splice(0))
    await rm(directory, { recursive: true, force: true })
})

const chinese = '中文测试，这是一段用于编码识别的中文文本。'
const gb18030 = Buffer.from(
  'd6d0cec4b2e2cad4a3acd5e2cac7d2bbb6ced3c3d3dab1e0c2ebcab6b1f0b5c4d6d0cec4cec4b1bea1a3',
  'hex'
)

test('自动识别 UTF-8、BOM 和有足够证据的旧编码，明确返回来源', () => {
  expect(decodeText(Buffer.from('中文\r\n第二行\r末行'))).toEqual({
    text: '中文\n第二行\n末行',
    encoding: 'utf-8',
    encodingSource: 'validated'
  })
  for (const [encoding, data] of [
    ['utf-8', Buffer.from('\ufeff中文')],
    ['utf-16le', Buffer.from('\ufeff中文', 'utf16le')],
    ['utf-16be', Buffer.from('\ufeff中文', 'utf16le').swap16()]
  ]) {
    expect(decodeText(data)).toEqual({ text: '中文', encoding, encodingSource: 'bom' })
  }
  expect(decodeText(gb18030)).toEqual({
    text: chinese,
    encoding: 'gb18030',
    encodingSource: 'detected'
  })
})

test('短旧编码文本不猜测；显式编码支持别名、UTF-16 无 BOM 和日文', () => {
  const shortChinese = Buffer.from([0xd6, 0xd0, 0xce, 0xc4])
  expect(() => decodeText(shortChinese)).toThrow(
    expect.objectContaining({
      problem: expect.objectContaining({
        code: 'ambiguous_text_encoding',
        message: expect.stringContaining('gb18030')
      })
    })
  )
  expect(decodeText(shortChinese, ' GBK ')).toEqual({
    text: '中文',
    encoding: 'gbk',
    encodingSource: 'explicit'
  })
  expect(decodeText(Buffer.from('hello 中文', 'utf16le'), 'utf-16le').text).toBe('hello 中文')
  expect(decodeText(Buffer.from([0x82, 0xa0]), 'shift_jis').text).toBe('あ')
})

test('不把 NUL 当作拒绝依据；错误编码和损坏的 BOM 文本明确报错', () => {
  expect(decodeText(Buffer.from('hello\0world')).text).toBe('hello\0world')
  expect(decodeText(Buffer.alloc(0)).text).toBe('')
  for (const [data, encoding, code] of [
    [Buffer.from([0xff]), 'utf-8', 'text_decoding_failed'],
    [Buffer.from('abc'), 'unknown-encoding', 'unsupported_text_encoding'],
    [Buffer.from([0xef, 0xbb, 0xbf, 0xff]), 'auto', 'text_decoding_failed'],
    [Buffer.from([0xff, 0xfe, 0, 0, 0x41, 0, 0, 0]), 'auto', 'unsupported_text_encoding'],
    [Buffer.from([0xff]), 'auto', 'ambiguous_text_encoding']
  ]) {
    expect(() => decodeText(data, encoding)).toThrow(
      expect.objectContaining({
        problem: expect.objectContaining({ code })
      })
    )
  }
})

async function fileFixture() {
  const directory = await realpath(await mkdtemp(path.join(os.tmpdir(), 'text-file-')))
  directories.push(directory)
  const policy = { readMode: 'roots', readableRoots: [], maxReadBytes: 100000 }
  const tools = fileTools({
    settings: { get: async () => ({ tools: { files: policy } }) },
    assets: { workspaceDirectory: async () => directory },
    write: () => {}
  })
  async function call(name, input, signal) {
    const result = await tools
      .find((tool) => tool.definition.name === name)
      .bind({ chatId: 'test' })(input, undefined, signal)
    return result.value
  }
  return { directory, policy, call }
}

test('读取和搜索共用编码与换行；任意扩展名、长行和后续页都可读取', async () => {
  const { directory, call } = await fileFixture()
  await writeFile(
    path.join(directory, 'text.pdf'),
    Buffer.concat([gb18030, Buffer.from('\r\n'), gb18030])
  )
  const first = await call('read_file_content', { file_path: 'text.pdf', end_line: 1 })
  expect(first).toMatchObject({
    content: chinese,
    totalLines: 2,
    encoding: 'gb18030',
    encodingSource: 'detected',
    next: { start_line: 2, start_column: 0 }
  })
  const second = await call('read_file_content', {
    file_path: 'text.pdf',
    ...first.next,
    encoding: first.encoding
  })
  expect(second).toMatchObject({ content: chinese, encodingSource: 'explicit', next: null })
  const search = await call('grep_search_in_file', {
    file_path: 'text.pdf',
    query: '编码',
    encoding: first.encoding
  })
  expect(search).toMatchObject({
    encoding: 'gb18030',
    encodingSource: 'explicit',
    matches: [
      { line: 1, text: chinese },
      { line: 2, text: chinese }
    ],
    nextLine: null
  })

  await writeFile(
    path.join(directory, 'long.custom'),
    Buffer.from('\ufeff' + '文'.repeat(15000) + '\r\nneedle', 'utf16le')
  )
  const long = await call('read_file_content', { file_path: 'long.custom' })
  expect(long.content).toBe('文'.repeat(12000))
  expect(long.next).toEqual({ start_line: 1, start_column: 12000 })
  const rest = await call('read_file_content', {
    file_path: 'long.custom',
    ...long.next,
    encoding: long.encoding
  })
  expect(rest.content).toBe('文'.repeat(3000) + '\nneedle')
  expect(
    (await call('grep_search_in_file', { file_path: 'long.custom', query: 'needle' })).matches
  ).toEqual([{ line: 2, text: 'needle' }])
})

test('读取上限、非文件检查和取消继续适用于两种工具', async () => {
  const { directory, policy, call } = await fileFixture()
  await writeFile(path.join(directory, 'large'), '12345')
  policy.maxReadBytes = 4
  for (const name of ['read_file_content', 'grep_search_in_file']) {
    await expect(call(name, { file_path: 'large', query: '1' })).rejects.toHaveProperty(
      'problem.code',
      'file_too_large'
    )
    await expect(call(name, { file_path: '.', query: '1' })).rejects.toHaveProperty(
      'problem.code',
      'file_not_regular'
    )
    const controller = new AbortController()
    controller.abort()
    await expect(
      call(name, { file_path: 'large', query: '1' }, controller.signal)
    ).rejects.toHaveProperty('name', 'AbortError')
  }
})

test('读取和搜索都附带候选预览，显式重试才返回正式结果', async () => {
  const { directory, call } = await fileFixture()
  await writeFile(path.join(directory, 'short.txt'), Buffer.from([0xd6, 0xd0, 0xce, 0xc4]))
  for (const name of ['read_file_content', 'grep_search_in_file']) {
    await expect(call(name, { file_path: 'short.txt', query: '中文' })).rejects.toMatchObject({
      problem: {
        code: 'ambiguous_text_encoding',
        candidates: expect.arrayContaining([
          expect.objectContaining({ encoding: 'gb18030', preview: '中文' })
        ])
      }
    })
    const result = await call(name, { file_path: 'short.txt', query: '中文', encoding: 'gb18030' })
    expect(result.encodingSource).toBe('explicit')
  }
})
