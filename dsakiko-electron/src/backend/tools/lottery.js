import { parameters, textParameter } from './definition.js'

/**
 * 抽签需要支持交互的客户端显式启用；随机结果由后端生成并持久保存。
 * @param {{create: Function}} lottery 对话持久抽签能力。
 * @returns {object} 抽签工具条目。
 */
export function lotteryTool(lottery) {
  return {
    frontend: true,
    definition: {
      name: 'start_lottery',
      description:
        '在界面上创建抽签卡片，供对方点击抽取随机结果。调用后立即返回 accepted（本轮不会产出抽中结果）；请在本轮台词中自然引导对方点击开始抽签，严禁自行编造抽签结果。实际抽中结果将在对方点击后通过后续轮次的 async_tool_results 提供。',
      mode: 'async',
      dependencies: [],
      requiredCapabilities: ['interactions'],
      resultSchema: { type: 'object' },
      parametersSchema: parameters(
        {
          title: {
            ...textParameter,
            description: '抽签主题或标题（例如“今晚吃什么”）。'
          },
          options: {
            type: 'array',
            minItems: 2,
            maxItems: 30,
            uniqueItems: true,
            items: textParameter,
            description: '候选签项列表（2 到 30 个互不相同的非空选项）。'
          }
        },
        ['title', 'options']
      )
    },
    bind:
      ({ chatId }) =>
      (input, source, signal) => {
        signal?.throwIfAborted()
        return lottery.create(chatId, input, source)
      }
  }
}
