import { afterEach, expect, test } from 'vitest'
import { startHttpServer } from '../../src/server/http-server.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

test('存活探测无需业务读盘，运行身份稳定且重启变化，探测凭据无业务权限', async () => {
  const backend = { assets: { releaseWhenPossible: async () => {} } }
  const first = await startHttpServer(backend, { origins: ['null'] })
  cleanups.push(() => first.close())
  const client = createBackendClient(first)
  cleanups.push(() => client.close())
  const status = await client.health()
  expect(status).toEqual({ backendRunId: first.healthProbe.backendRunId, status: 'running' })
  expect(await client.health()).toEqual(status)
  const response = await fetch(first.healthProbe.url, {
    headers: { Authorization: 'Bearer ' + first.healthProbe.token }
  })
  expect(await response.json()).toEqual(status)
  expect(response.headers.get('cache-control')).toBe('no-store')
  const unauthorized = await fetch(first.healthProbe.url)
  expect(unauthorized.ok).toBe(false)
  const business = await fetch(first.baseUrl + '/api/v1/call/settings/get', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + first.healthProbe.token },
    body: JSON.stringify({ args: [] })
  })
  expect((await business.json()).problem.code).toBe('unauthorized')
  const forbidden = await fetch(first.healthProbe.url, {
    headers: { Authorization: 'Bearer ' + first.token, Origin: 'https://untrusted.example' }
  })
  expect((await forbidden.json()).problem.code).toBe('forbidden_origin')
  const second = await startHttpServer(backend)
  cleanups.push(() => second.close())
  expect(second.healthProbe.backendRunId).not.toBe(status.backendRunId)
  client.close()
  await expect(client.health()).rejects.toHaveProperty('code', 'client_closed')
})
