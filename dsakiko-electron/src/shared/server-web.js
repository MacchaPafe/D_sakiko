/** @typedef {{url: string, title?: string}} WebSource 网页来源。 */

/**
 * @typedef {object} ServerWebCall
 * @property {string} id 调用标识。
 * @property {'web_search' | 'read_url'} toolName 对话工具名称。
 * @property {object} arguments 服务端提供的搜索动作或参数。
 * @property {{status: 'succeeded'} | {status: 'failed', problem: import('./contracts/common.js').Problem}} outcome 已有结果，无需本地执行。
 * @property {WebSource[]} sources 供对话展示的来源。
 */

/** @typedef {{calls: ServerWebCall[], citations: WebSource[]}} ServerWebActivity 单次响应中的服务端网页工具与引用。 */

/**
 * 投影 DeepSeek 的协议材料，现有存档与新响应使用同一份规则。
 * @param {object | undefined} native DeepSeek 消息级协议材料。
 * @returns {ServerWebActivity} 可展示的调用与引用。
 */
export function deepSeekWebActivity(native) {
  const messages = {
    unavailable: 'DeepSeek 网页工具暂时不可用。',
    url_not_accessible: 'DeepSeek 无法访问该网页。',
    url_not_allowed: 'DeepSeek 拒绝读取该网页。'
  }
  const calls = (native?.server_tool_calls ?? []).flatMap((step) =>
    (step.calls ?? []).map((call) => {
      const code = call.error?.code
      return {
        id: call.id ?? `${step.index}:${call.index}`,
        toolName: call.type === 'deepseek_read_url' ? 'read_url' : 'web_search',
        arguments: call.action ?? {},
        outcome:
          call.status === 'completed'
            ? { status: 'succeeded' }
            : {
                status: 'failed',
                problem: {
                  code: code ?? 'server_web_failed',
                  message: Object.hasOwn(messages, code)
                    ? messages[code]
                    : 'DeepSeek 网页工具调用未完成。',
                  retryable: false
                }
              },
        sources: call.page ? [call.page] : (call.sources ?? [])
      }
    })
  )
  const citations = (native?.annotations ?? []).map((item) => item.url_citation).filter(Boolean)
  return { calls, citations }
}
