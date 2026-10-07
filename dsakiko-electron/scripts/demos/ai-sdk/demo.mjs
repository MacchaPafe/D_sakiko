import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createModel } from './providers.mjs'
import { runAgent } from './agent.mjs'
import { automaticAgent, complete } from './operations.mjs'
import { createTimeTool, demoCharacters } from './schema.mjs'

function argument(name) {
  const prefix = `--${name}=`
  return process.argv.find((item) => item.startsWith(prefix))?.slice(prefix.length)
}

async function loadConnection() {
  const path = argument('legacy-config')
  if (path) {
    const legacy = JSON.parse(await readFile(path, 'utf8'))
    const config = legacy.llm_setting || {}
    if (config.enable_custom_llm_api_provider)
      throw new Error('旧自定义配置请用 AI_DEMO_* 显式提供协议。')
    const provider = config.llm_api_provider
    function selected(value) {
      if (typeof value === 'string') return value
      return value?.[provider] || ''
    }
    return {
      provider,
      apiKey: selected(config.llm_api_key),
      baseURL: selected(config.llm_api_base_url) || undefined,
      model: selected(config.llm_api_model).replace(new RegExp(`^${provider}/`), '')
    }
  }
  return {
    provider: process.env.AI_DEMO_PROVIDER || 'deepseek',
    apiKey: process.env.AI_DEMO_API_KEY,
    model: process.env.AI_DEMO_MODEL,
    baseURL: process.env.AI_DEMO_BASE_URL || undefined,
    providerOptions: JSON.parse(process.env.AI_DEMO_PROVIDER_OPTIONS || '{}')
  }
}

async function main() {
  if (process.argv.includes('--help')) {
    console.log(
      'AI_DEMO_PROVIDER / AI_DEMO_MODEL / AI_DEMO_API_KEY / AI_DEMO_BASE_URL / AI_DEMO_PROVIDER_OPTIONS'
    )
    console.log(
      'node demo.mjs --mode=all|completion|automatic|controlled [--legacy-config=/绝对路径/d_sakiko_config.json]'
    )
    return
  }
  const mode = argument('mode') || 'all'
  if (!['all', 'completion', 'automatic', 'controlled'].includes(mode))
    throw new Error('未知 demo 模式。')
  const connection = structuredClone(await loadConnection())
  const model = createModel(connection)
  // 只提取厂商的错误说明并屏蔽当前密钥，不序列化 SDK 错误对象。
  async function reportFailure(error) {
    let serverMessage
    try {
      const message = JSON.parse(error.responseBody)?.error?.message
      if (typeof message === 'string') {
        serverMessage = message.replaceAll(connection.apiKey, '[redacted]').slice(0, 800)
      }
    } catch {
      /* 非 JSON 的错误响应不直接回显。 */
    }
    console.error(
      JSON.stringify({
        status: 'failed',
        error: error.name,
        statusCode: error.statusCode,
        serverMessage
      })
    )
    process.exitCode = 1
  }
  try {
    console.log(JSON.stringify({ provider: connection.provider, model: connection.model }))
    if (mode === 'all' || mode === 'completion') {
      const text = await complete(model, AbortSignal.timeout(90000), connection.providerOptions)
      console.log(JSON.stringify({ scenario: 'completion', text }))
    }
    if (mode === 'all' || mode === 'automatic') {
      const result = await automaticAgent({
        model,
        definition: createTimeTool(),
        characters: demoCharacters,
        signal: AbortSignal.timeout(90000),
        providerOptions: connection.providerOptions
      })
      console.log(JSON.stringify({ scenario: 'automatic', ...result }))
    }
    if (mode === 'all' || mode === 'controlled') {
      const events = []
      let executions = 0
      const definition = createTimeTool()
      const execute = definition.execute
      definition.execute = async (input, signal) => {
        executions += 1
        events.push('execute')
        return execute(input, signal)
      }
      const result = await runAgent({
        model,
        characters: demoCharacters,
        tools: [definition],
        prompt: '请先查询时间，然后用一句中文问候我。',
        signal: AbortSignal.timeout(90000),
        timeoutMs: 90000,
        providerOptions: connection.providerOptions,
        observer: {
          async onIntermediate(messages) {
            events.push(...messages.map((message) => message.kind))
          },
          async onToolActivity(record) {
            events.push(`${record.callId}:${record.status}`)
          }
        }
      })
      assert.equal(result.status, 'completed')
      assert.equal(executions, 1)
      const running = events.findIndex((event) => event.endsWith(':running'))
      const succeeded = events.findIndex((event) => event.endsWith(':succeeded'))
      assert.ok(
        running >= 0 && running < events.indexOf('execute') && succeeded > events.indexOf('execute')
      )
      console.log(JSON.stringify({ scenario: 'controlled', executions, events, ...result }))
    }
  } catch (error) {
    await reportFailure(error)
  }
}

// SDK 错误对象可能携带请求头和请求体，命令行只输出稳定类别。
main().catch((error) => {
  console.error(
    JSON.stringify({
      status: 'failed',
      error: error.name,
      hint: '检查模型能力、连接、凭据及网络；测试详细断言见 npm test。'
    })
  )
  process.exitCode = 1
})
