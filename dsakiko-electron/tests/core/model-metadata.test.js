import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { afterEach, expect, test, vi } from 'vitest'
import { ModelCatalog, MODEL_CATALOG_URL } from '../../src/backend/llm/model-catalog.js'
import { Settings } from '../../src/backend/settings/interface.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
const day = 86400000
const connection = { provider: 'deepseek', baseUrl: 'https://api.deepseek.com/v1' }
const model = { id: 'exact-model' }
const directoryData = {
  deepseek: {
    models: { 'exact-model': { limit: { context: 1048576, input: 1000000, output: 64000 } } }
  }
}
const response = () => new Response(JSON.stringify(directoryData))
async function fixture(transport = vi.fn(response)) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'model-catalog-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  let now = 1800000000000
  const catalog = new ModelCatalog({ directory, fetch: transport, now: () => now })
  cleanups.push(() => catalog.close())
  return {
    directory,
    catalog,
    transport,
    advance: (time) => {
      now += time
    }
  }
}

test('首次不等待下载、合并并发请求；新鲜缓存不联网且重启后可离线读取', async () => {
  let release
  const transport = vi.fn(
    () =>
      new Promise((resolve) => {
        release = resolve
      })
  )
  const { directory, catalog } = await fixture(transport)
  expect(await catalog.lookup(connection, model)).toEqual({})
  expect(await catalog.lookup(connection, model)).toEqual({})
  expect(transport).toHaveBeenCalledTimes(1)
  expect(transport.mock.calls[0][0]).toBe(MODEL_CATALOG_URL)
  release(response())
  await catalog.pending
  expect(await catalog.lookup(connection, model)).toMatchObject({
    contextTokens: 1048576,
    inputTokens: 1000000,
    outputTokens: 64000
  })
  expect(transport).toHaveBeenCalledTimes(1)
  const offline = new ModelCatalog({
    directory,
    fetch: vi.fn(() => {
      throw Error('offline')
    }),
    now: () => 1800000000001
  })
  cleanups.push(() => offline.close())
  expect(await offline.lookup(connection, model)).toHaveProperty('contextTokens', 1048576)
  expect(offline.transport).not.toHaveBeenCalled()
})

test('三天后刷新失败仍信任旧上限，失败退避；坏目录不替换已保存数据', async () => {
  const { directory, catalog, transport, advance } = await fixture()
  await catalog.lookup(connection, model)
  await catalog.pending
  const before = await readFile(path.join(directory, 'models-dev.json'), 'utf8')
  advance(3 * day)
  transport.mockRejectedValueOnce(Error('offline'))
  expect(await catalog.lookup(connection, model)).toHaveProperty('contextTokens', 1048576)
  await catalog.pending
  await catalog.lookup(connection, model)
  expect(transport).toHaveBeenCalledTimes(2)
  advance(60001)
  transport.mockResolvedValueOnce(
    new Response('{"deepseek":{"models":{"bad":{"limit":{"context":-1}}}}}')
  )
  await catalog.lookup(connection, model)
  await catalog.pending
  expect(await readFile(path.join(directory, 'models-dev.json'), 'utf8')).toBe(before)
  expect(await catalog.lookup(connection, model)).toHaveProperty('contextTokens', 1048576)
})

test('自定义端点不按同名猜测，显式映射可查；手工覆盖高于目录', async () => {
  const { directory, catalog, transport } = await fixture()
  const proxy = { ...connection, baseUrl: 'https://proxy.invalid/v1' }
  expect(await catalog.lookup(proxy, model)).toEqual({})
  expect(transport).not.toHaveBeenCalled()
  const mapped = {
    id: 'deployment',
    catalogMapping: { provider: 'deepseek', model: 'exact-model' },
    declared: { contextTokens: null },
    overrides: {}
  }
  await catalog.lookup(proxy, mapped)
  await catalog.pending
  const settings = new Settings({
    directory,
    assets: { registerOwnerReader() {} },
    modelCatalog: catalog
  })
  let saved = await settings.saveConnection(
    {
      ...proxy,
      id: 'test',
      revision: 0,
      name: 'test',
      protocol: 'chat-completions',
      models: [mapped]
    },
    { kind: 'keep' }
  )
  expect(
    (await settings.resolveConnection({ connectionId: 'test', modelId: 'deployment' })).capabilities
      .contextTokens
  ).toBe(1048576)
  saved.models[0].overrides.contextTokens = 128000
  await settings.saveConnection(saved, { kind: 'keep' })
  expect(
    (await settings.resolveConnection({ connectionId: 'test', modelId: 'deployment' })).capabilities
  ).toMatchObject({ contextTokens: 128000, contextSource: 'manual-override' })
})

test('损坏缓存不阻塞首次查询，成功下载可修复', async () => {
  const { directory, catalog } = await fixture()
  await writeFile(path.join(directory, 'models-dev.json'), '{broken')
  expect(await catalog.lookup(connection, model)).toEqual({})
  await catalog.pending
  expect(await catalog.lookup(connection, model)).toHaveProperty('contextTokens', 1048576)
})
