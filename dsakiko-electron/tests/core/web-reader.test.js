import { expect, test, vi } from 'vitest'
import http from 'node:http'
import { Readable } from 'node:stream'
import { EventEmitter } from 'node:events'
import { webReaderTool } from '../../src/backend/tools/web-reader.js'

test('本地阅读网页按轮次分页，提取中文正文并解析相对链接', async () => {
  const transport = vi.spyOn(http, 'get').mockImplementation((_url, _options, onResponse) => {
    const page = Readable.from([
      Buffer.from(
        '<title>排练</title><article>开始<a href="/score">乐谱</a>' +
          '歌'.repeat(6500) +
          '</article>'
      )
    ])
    page.statusCode = 200
    page.headers = { 'content-type': 'text/html; charset=utf-8' }
    onResponse(page)
    return new EventEmitter()
  })
  try {
    const read = webReaderTool().bind()
    const url = 'http://93.184.216.34/rehearsal'
    const { value: first } = await read({ url })
    expect(first).toMatchObject({ title: '排练', next_offset: 6000 })
    expect(first.content).toContain('开始乐谱 (http://93.184.216.34/score)')
    const { value: next } = await read({ url, offset: first.next_offset })
    expect(next.next_offset).toBeNull()
    expect(next.content).toMatch(/^歌+$/)
    expect(transport).toHaveBeenCalledTimes(1)
    await expect(read({ url, offset: 10000 })).rejects.toHaveProperty(
      'problem.code',
      'invalid_offset'
    )
  } finally {
    transport.mockRestore()
  }
})
test.each(['http://127.0.0.1', 'http://[::1]', 'http://[::ffff:127.0.0.1]', 'file:///etc/hosts'])(
  '拒绝非公网网页 %s',
  async (url) => {
    await expect(webReaderTool().bind()({ url })).rejects.toThrow()
  }
)
