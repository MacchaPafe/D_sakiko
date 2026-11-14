import { afterEach, expect, test } from 'vitest'
import WebSocket from 'ws'
import { startHttpServer } from '../../src/server/http-server.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function fixture(origins) {
  const server = await startHttpServer(
    { assets: { releaseWhenPossible: async () => {} } },
    { origins }
  )
  cleanups.push(() => server.close())
  return server
}
async function ticket(server, origin) {
  const headers = { Authorization: 'Bearer ' + server.token }
  if (origin !== undefined) headers.Origin = origin
  const response = await fetch(server.baseUrl + '/api/v1/events-ticket', {
    method: 'POST',
    headers
  })
  return (await response.json()).value.url
}
async function accepts(url, origin) {
  return new Promise((resolve) => {
    const socket = new WebSocket(url, { origin })
    cleanups.push(() => socket.terminate())
    socket.once('open', () => {
      socket.close()
      resolve(true)
    })
    socket.once('error', () => resolve(false))
  })
}

test('桌面票据支持缺少 Origin 或 null 的 fetch 与 file:// 握手，仍为一次性票据', async () => {
  const server = await fixture(['null', 'file://'])
  for (const origin of [undefined, 'null']) {
    const url = await ticket(server, origin)
    expect(await accepts(url, 'file://')).toBe(true)
    expect(await accepts(url, 'file://')).toBe(false)
  }
})

test('普通网页来源不参与桌面归一化，允许的不同网页之间也不能交换票据', async () => {
  const first = 'https://first.example'
  const second = 'https://second.example'
  const server = await fixture(['null', 'file://', first, second])
  expect(await accepts(await ticket(server, first), second)).toBe(false)
  expect(await accepts(await ticket(server, first), 'file://')).toBe(false)
  expect(await accepts(await ticket(server), 'https://untrusted.example')).toBe(false)
  const restricted = await fixture(['null'])
  expect(await accepts(await ticket(restricted, 'null'), 'file://')).toBe(false)
})
