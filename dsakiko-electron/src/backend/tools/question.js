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
          '可选的预设候选项列表（最多 30 项）；提供时展示为选项卡片，省略时由对方自由输入文本。'
      }
    },
    ['question']
  ),
  resultSchema: { type: 'string', minLength: 1 }
}

/** 用户交互完成后通过既有对话队列交付，不挂起当前生成。 */
export function questionTool() {
  return {
    definition: {
      name: 'ask_user',
      description:
        '在界面上向对方发起结构化提问卡片（适用于需要明确抉择、问卷选项或正式确认的场景；日常闲聊请直接在台词中提问）。调用后立即返回 accepted，请在本轮最后正常输出台词引导对方作答；对方的回答将在后续轮次通过 async_tool_results 返回。',
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
