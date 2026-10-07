/** 厂商报文夹具只替换 fetch；generateText、参数校验和协议适配使用真实 SDK。 */
export function scriptedFetch(responses) {
  const requests = []
  async function fetcher(url, options) {
    options.signal?.throwIfAborted()
    const request = {
      url: String(url),
      body: JSON.parse(options.body),
      headers: new Headers(options.headers),
      signal: options.signal
    }
    requests.push(request)
    const next = responses.shift()
    if (next === undefined) throw new Error('意外增加了模型请求。')
    if (typeof next === 'function') return next(request)
    return new Response(JSON.stringify(next), { headers: { 'content-type': 'application/json' } })
  }
  return { fetcher, requests }
}

export function chatReply(text = '', reasoning = '', calls = []) {
  return {
    id: 'response-demo',
    created: 1,
    model: 'deepseek-v4-flash',
    object: 'chat.completion',
    choices: [
      {
        index: 0,
        finish_reason: calls.length ? 'tool_calls' : 'stop',
        message: {
          role: 'assistant',
          content: text || null,
          reasoning_content: reasoning,
          ...(calls.length ? { tool_calls: calls } : {})
        }
      }
    ],
    usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 }
  }
}

export function toolCall(id = 'time-1', name = 'get_current_time', input = '{}') {
  return { id, type: 'function', function: { name, arguments: input } }
}

export function anthropicReply(content) {
  return {
    id: 'msg-demo',
    type: 'message',
    role: 'assistant',
    model: 'claude-demo',
    content,
    stop_reason: content.some((part) => part.type === 'tool_use') ? 'tool_use' : 'end_turn',
    stop_sequence: null,
    usage: { input_tokens: 10, output_tokens: 10 }
  }
}

export function googleReply(parts) {
  return {
    candidates: [{ index: 0, content: { role: 'model', parts }, finishReason: 'STOP' }],
    usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 10, totalTokenCount: 20 }
  }
}

export const finalValue = {
  lines: [
    {
      speakerId: 'sakiko',
      text: '晚上好。',
      translation: null,
      emotion: null,
      performance: { motion: 'auto', expression: 'auto' }
    }
  ]
}

export const finalText = JSON.stringify(finalValue)

export function deferred() {
  let resolve
  const promise = new Promise((accept) => {
    resolve = accept
  })
  return { promise, resolve }
}
