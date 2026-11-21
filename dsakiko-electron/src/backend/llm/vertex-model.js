import { AsyncLocalStorage } from 'node:async_hooks'
import { createGoogleVertex } from '@ai-sdk/google-vertex'
import { wrapLanguageModel } from 'ai'
import { requireValue } from '../core/support.js'
import { vertexFields, vertexPrivateKey } from '../settings/vertex.js'

const prompts = new AsyncLocalStorage()
const providers = new WeakMap()

/** 当前 Vertex SDK 在编码时省略函数 ID。Argon 使用 ID 匹配并行调用，按原消息次序补回。 */
function argonTransport(transport) {
  return (url, options) => {
    if (!String(url).includes('/models/gemini-4-argon:')) return transport(url, options)
    const body = JSON.parse(options.body)
    const config = body.generationConfig ?? {}
    delete config.temperature
    delete config.topP
    if (config.thinkingConfig?.thinkingLevel)
      config.thinkingConfig.thinkingLevel = config.thinkingConfig.thinkingLevel.toUpperCase()
    const parts = (prompts.getStore() ?? []).flatMap((message) =>
      Array.isArray(message.content) ? message.content : []
    )
    const calls = parts.filter((part) => part.type === 'tool-call')
    const results = parts.filter((part) => part.type === 'tool-result')
    let callIndex = 0,
      resultIndex = 0
    for (const message of body.contents)
      for (const part of message.parts) {
        let wire, original
        if (part.functionCall) {
          wire = part.functionCall
          original = calls[callIndex++]
        }
        if (part.functionResponse) {
          wire = part.functionResponse
          original = results[resultIndex++]
        }
        if (wire) {
          requireValue(
            original?.toolName === wire.name && original.toolCallId,
            'invalid_tool_history',
            '工具结果与调用不匹配。'
          )
          wire.id = original.toolCallId
        }
      }
    return transport(url, { ...options, body: JSON.stringify(body) })
  }
}

/** @param {object} connection 模型连接。 @param {string} secret 服务账号私钥。 @param {typeof fetch} transport 网络传输。 @returns {object} 使用服务账号自动续期的 Vertex SDK 实例。 */
export function vertexProvider(connection, secret, transport) {
  const { project, location, email } = vertexFields(connection.vertex)
  const id = connection.connectionId ?? connection.id
  let cache = providers.get(transport)
  if (!cache) {
    cache = new Map()
    providers.set(transport, cache)
  }
  const previous = cache.get(id)
  if (
    previous &&
    previous.project === project &&
    previous.location === location &&
    previous.email === email &&
    previous.secret === secret
  )
    return previous.provider
  const provider = createGoogleVertex({
    project,
    location,
    apiKey: '',
    googleAuthOptions: {
      credentials: { client_email: email, private_key: vertexPrivateKey(secret) }
    },
    fetch: argonTransport(transport)
  })
  cache.set(id, { provider, project, location, email, secret })
  if (cache.size > 8) cache.delete(cache.keys().next().value)
  return provider
}

/** @param {object} model Vertex 模型。 @returns {object} 保留每次并发请求各自工具 ID 的模型。 */
export function argonModel(model) {
  return wrapLanguageModel({
    model,
    middleware: {
      wrapGenerate: ({ doGenerate, params }) => prompts.run(params.prompt, doGenerate),
      wrapStream: ({ doStream, params }) => prompts.run(params.prompt, doStream)
    }
  })
}
