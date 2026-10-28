/**
 * 解析有效预算。已知模型窗口替代应用兜底值，默认摘要在窗口的 90% 触发。
 * @param {object} request 模型连接与生成选项。
 * @returns {{budget: number, reservedOutputTokens: number, inputBudget: number, thresholdTokens: number}} 本轮固定的预算。
 */
export function contextBudget(request) {
  const capabilities = request.connection.capabilities ?? {}
  const options = request.options
  const budget = capabilities.contextTokens ?? options.contextTokenBudget
  const reservedOutputTokens = Math.min(
    options.maxOutputTokens,
    capabilities.outputTokens ?? Infinity
  )
  const inputBudget = Math.min(budget - reservedOutputTokens, capabilities.inputTokens ?? Infinity)
  const configured = options.compression.thresholdTokens
  // 旧版本默认阈值在读取时升级；用户自定义阈值仍按绝对值保留。
  const automatic = configured === 258000 || configured === 270000
  const thresholdTokens = Math.min(automatic ? Math.floor(budget * 0.9) : configured, inputBudget)
  return { budget, reservedOutputTokens, inputBudget, thresholdTokens }
}

/**
 * 只裁剪本次投影，摘要与普通 Turn 分开保护；重建消息时会重新校验协议指纹。
 * @param {object} request 可修改 history 的请求副本。
 * @param {(request: object) => Promise<object>} inspect 真实请求预算检查。
 * @returns {Promise<{inspection: object, removedTurnIds: string[]}>} 最终诊断及被裁剪的普通轮次。
 */
export async function trimHistory(request, inspect) {
  let inspection = await inspect(request)
  const removedTurnIds = []
  while (inspection.fit === 'over-budget') {
    const index = request.history.findIndex((turn) =>
      turn.messages.some((message) => message.kind !== 'abstract')
    )
    if (index < 0) break
    const turn = request.history[index]
    const abstracts = turn.messages.filter((message) => message.kind === 'abstract')
    const next = request.history.slice()
    if (abstracts.length) next[index] = { ...turn, messages: abstracts }
    else next.splice(index, 1)
    request.history = next
    removedTurnIds.push(turn.turnId)
    inspection = await inspect(request)
  }
  return { inspection, removedTurnIds }
}
