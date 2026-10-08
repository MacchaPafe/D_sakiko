import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createGateway, generateText, uploadFile } from 'ai'
import { createAnthropic } from '@ai-sdk/anthropic'
import { createDeepSeek } from '@ai-sdk/deepseek'
import { createGoogleGenerativeAI } from '@ai-sdk/google'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { chatReply, scriptedFetch } from './fixtures.mjs'

const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHosAAAAASUVORK5CYII=',
  'base64'
)

test('模型构造和 supportedUrls 不验证模型存在或视觉能力', async () => {
  let requests = 0
  async function rejectNetwork() {
    requests += 1
    throw new Error('本实验不允许网络请求。')
  }
  const options = { apiKey: 'synthetic-test-key', fetch: rejectNetwork }
  const anthropic = createAnthropic(options)
  const deepseek = createDeepSeek(options)

  assert.deepEqual(
    await anthropic('model-that-does-not-exist').supportedUrls,
    await anthropic('claude-sonnet-4-5').supportedUrls
  )
  assert.deepEqual(
    await deepseek('model-that-does-not-exist').supportedUrls,
    await deepseek('deepseek-v4-pro').supportedUrls
  )
  assert.equal(requests, 0)
})

test('supportedUrls 为空仍可组装内联图片请求，不能据此判定不支持视觉', async () => {
  const transport = scriptedFetch([chatReply('夹具响应。')])
  const provider = createOpenAICompatible({
    name: 'capability-experiment',
    baseURL: 'https://example.invalid/v1',
    apiKey: 'synthetic-test-key',
    fetch: transport.fetcher
  })
  const model = provider('model-that-does-not-exist')
  assert.deepEqual(await model.supportedUrls, {})

  await generateText({
    model,
    messages: [{ role: 'user', content: [{ type: 'file', data: png, mediaType: 'image/png' }] }],
    maxRetries: 0,
    telemetry: { isEnabled: false }
  })

  const part = transport.requests[0].body.messages[0].content[0]
  assert.equal(part.type, 'image_url')
  assert.match(part.image_url.url, /^data:image\/png;base64,/)
})

test('锁定版本的 Gateway SDK 模型目录会丢弃能力标签和输入模态字段', async () => {
  const requests = []
  const provider = createGateway({
    apiKey: 'synthetic-test-key',
    baseURL: 'https://example.invalid/v4/ai',
    async fetch(url) {
      requests.push(String(url))
      return Response.json({
        models: [
          {
            id: 'example/vision-model',
            name: '实验目录模型',
            modelType: 'language',
            specification: {
              specificationVersion: 'v4',
              provider: 'example',
              modelId: 'vision-model'
            },
            tags: ['vision', 'file-input'],
            inputModalities: ['text', 'image', 'file']
          }
        ]
      })
    }
  })

  const { models } = await provider.getAvailableModels()
  assert.deepEqual(requests, ['https://example.invalid/v4/ai/config'])
  assert.equal(models[0].id, 'example/vision-model')
  assert.equal(Object.hasOwn(models[0], 'tags'), false)
  assert.equal(Object.hasOwn(models[0], 'inputModalities'), false)
})

test('files 是供应商级接口，demo 的三种直连适配器提供上传而兼容适配器未提供', () => {
  const options = { apiKey: 'synthetic-test-key' }
  const providers = [
    createDeepSeek(options),
    createAnthropic(options),
    createGoogleGenerativeAI(options)
  ]
  for (const provider of providers) {
    assert.equal(typeof provider.files, 'function')
    assert.equal(typeof provider.files().uploadFile, 'function')
  }
  const compatible = createOpenAICompatible({
    ...options,
    name: 'capability-experiment',
    baseURL: 'https://example.invalid/v1'
  })
  assert.equal(compatible.files, undefined)
})

test('统一 uploadFile 可走 DeepSeek 图片上传，但同一接口拒绝 PDF', async () => {
  const requests = []
  const provider = createDeepSeek({
    apiKey: 'synthetic-test-key',
    baseURL: 'https://example.invalid',
    async fetch(url, options) {
      requests.push({ url: String(url), form: options.body })
      return Response.json({ id: 'file-fixture' })
    }
  })

  const result = await uploadFile({
    api: provider.files(),
    data: png,
    mediaType: 'image/png',
    filename: 'pixel.png'
  })
  assert.deepEqual(result.providerReference, { deepseek: 'file-fixture' })
  assert.equal(requests[0].url, 'https://example.invalid/files')
  assert.equal(requests[0].form.get('purpose'), 'user_data')
  assert.equal(requests[0].form.get('file').name, 'pixel.png')

  await assert.rejects(
    uploadFile({
      api: provider.files(),
      data: Buffer.from('%PDF-1.4\n'),
      mediaType: 'application/pdf',
      filename: 'sample.pdf'
    }),
    /DeepSeek file uploads support JPEG, PNG, GIF, and WebP images/
  )
  assert.equal(requests.length, 1)
})
