import { generateText, isStepCount, Output, ToolLoopAgent, tool } from 'ai'
import { validatedSchema } from './agent.mjs'
import { replySchema, validateReply } from './schema.mjs'

/**
 * 普通非流式补全，使用厂商对象直连服务。
 * @param {import('ai').LanguageModel} model 已选择的厂商模型。
 * @param {AbortSignal} signal 取消信号。
 * @param {object} [providerOptions] 厂商选项。
 * @returns {Promise<string>} 完整文本。
 */
export async function complete(model, signal, providerOptions) {
  const result = await generateText({
    model,
    prompt: '请只回复“连接成功”。',
    maxOutputTokens: 2048,
    maxRetries: 0,
    abortSignal: signal,
    providerOptions,
    telemetry: { isEnabled: false }
  })
  if (!result.text.trim()) throw new Error('模型未返回补全文本。')
  return result.text
}

/**
 * 验证 SDK 自动执行工具，并在同一循环返回结构化台词。
 * 此入口不承诺项目 Observer 顺序，正式接入参考 runAgent。
 * @param {object} options 自动循环选项。
 * @param {import('ai').LanguageModel} options.model 固定厂商模型。
 * @param {import('../../../src/backend/llm/interface.js').AgentTool} options.definition 时间工具。
 * @param {import('../../../src/backend/llm/interface.js').AgentCharacter[]} options.characters 角色快照。
 * @param {AbortSignal} options.signal 取消信号。
 * @param {object} [options.providerOptions] 厂商选项。
 * @returns {Promise<object>} 台词、实际执行次数和 SDK 步数。
 */
export async function automaticAgent({ model, definition, characters, signal, providerOptions }) {
  let executions = 0
  const agent = new ToolLoopAgent({
    model,
    providerOptions,
    maxRetries: 0,
    maxOutputTokens: 4096,
    telemetry: { isEnabled: false },
    stopWhen: isStepCount(4),
    instructions: `你为这些角色生成一句中文问候：${JSON.stringify(characters)}。先查询时间，然后按输出 Schema 回复，演出使用 auto，translation 和 emotion 为 null。`,
    tools: {
      [definition.name]: tool({
        description: definition.description,
        inputSchema: validatedSchema(definition.parametersSchema),
        async execute(input, { abortSignal }) {
          executions += 1
          return definition.execute(input, abortSignal)
        }
      })
    },
    prepareStep({ stepNumber }) {
      // DeepSeek 思考模式拒绝强制 tool_choice；靠提示请求调用，再核对执行次数。
      if (stepNumber === 0) return { toolChoice: 'auto' }
      return { activeTools: [] }
    },
    output: Output.object({ schema: replySchema })
  })
  const response = await agent.generate({
    prompt: '现在是什么时间？请用一句话问候我。',
    abortSignal: signal
  })
  const lines = validateReply(response.output, characters)
  if (executions !== 1) throw new Error('自动循环未恰好执行一次时间工具。')
  return { lines, executions, steps: response.steps.length }
}
