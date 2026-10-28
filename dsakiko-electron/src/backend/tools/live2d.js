import { parameters, syncTool, textParameter } from './definition.js'

/**
 * 服装选择在后端保存，前端独立消费场景更新，不等待渲染完成。
 * @param {{list: Function, change: Function}} live2d 绑定对话的服装能力。
 * @returns {object[]} 查询与切换工具条目。
 */
export function live2dTools(live2d) {
  return [
    syncTool(
      {
        name: 'fetch_all_live2d_models',
        description: '列出当前对话角色在当前形态下的可用服装（modelId 与名称）及当前选中项。',
        requiredCapabilities: ['live2d'],
        parametersSchema: parameters({
          character: {
            ...textParameter,
            description:
              '要查询的角色名（使用本轮参与角色的角色名）；省略时列出本对话全部参与角色的可用服装。'
          }
        })
      },
      ({ chatId }, input) => live2d.list(chatId, input.character)
    ),
    syncTool(
      {
        name: 'change_character_live2d',
        description:
          '为指定角色切换当前形态下的服装（需先调用 fetch_all_live2d_models 获取可用 modelId）。调用成功后即可在台词中按已换装的状态自然表现，切勿提及后端保存或前端加载等技术细节。',
        dependencies: ['fetch_all_live2d_models'],
        requiredCapabilities: ['live2d'],
        parametersSchema: parameters(
          {
            character: {
              ...textParameter,
              description: '目标角色的角色名（必须使用本轮参与角色的角色名）。'
            },
            modelId: {
              ...textParameter,
              description:
                '要切换到的服装 ID（从 fetch_all_live2d_models 返回的 models 列表中选取）。'
            }
          },
          ['character', 'modelId']
        )
      },
      ({ chatId }, input, source) => live2d.change(chatId, input, source)
    )
  ]
}
