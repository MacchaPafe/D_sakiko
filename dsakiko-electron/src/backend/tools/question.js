import { parameters, textParameter } from './definition.js'

export const questionKind = {
  requestSchema: parameters(
    {
      question: {
        ...textParameter,
        description: '要向对方展示的问题正文。'
      },
      choices: {
        type: 'array',
        maxItems: 30,
        items: textParameter,
        description:
          '可选的预设候选项列表（最多 30 项）；无论是否提供候选项，对方均可自由输入答案或跳过问题。'
      }
    },
    ['question']
  ),
  resultSchema: {
    oneOf: [
      parameters(
        {
          kind: { const: 'answered' },
          answer: { ...textParameter, pattern: '\\S' }
        },
        ['kind', 'answer']
      ),
      parameters({ kind: { const: 'skipped' } }, ['kind'])
    ]
  }
}

/** 用户交互完成后通过既有对话队列交付，不挂起当前生成。 */
export function questionTool() {
  return {
    definition: {
      name: 'ask_user',
      description:
        '在界面上向对方发起提问卡片（适用于需要明确抉择、问卷选项或正式确认的场景；日常闲聊请直接在台词中提问）。调用后立即返回 accepted，请在本轮最后正常输出台词引导对方作答；对方始终可以选择候选项、自由输入或跳过；后续轮次通过 async_tool_results 返回 { kind: "answered", answer: "回答" } 或 { kind: "skipped" }。跳过表示未提供答案，不代表同意、否定或默认选择，请自然继续而非反复追问。',
      dependencies: [],
      mode: 'async',
      parametersSchema: questionKind.requestSchema,
      resultSchema: questionKind.resultSchema,
      requiredCapabilities: ['interactions']
    },
    frontend: true,
    bind:
      ({ interactions }) =>
      (payload, source, signal) =>
        interactions.request(
          {
            requestId: source.turnId + ':' + source.callId,
            kind: 'question',
            source,
            payload
          },
          signal
        )
  }
}
