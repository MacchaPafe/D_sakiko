import { datetimeTool } from './datetime.js'
import { systemTool } from './system.js'
import { fileTools } from './files.js'
import { weatherTool } from './weather.js'
import { webSearchTool } from './web-search.js'
import { questionTool, questionKind } from './question.js'
import { reminderTool } from './reminder.js'
import { live2dTools } from './live2d.js'
import { lotteryTool } from './lottery.js'

export const interactionKinds = { question: questionKind }

/**
 * 汇总内置工具，装配入口只提供具名能力，不包含工具的执行细节。
 * @param {object} dependencies 后端配置、资源、网络和对话绑定回调。
 * @returns {object[]} 可注册的工具条目。
 */
export function createBuiltinTools({
  settings,
  assets,
  transport,
  writeFile,
  live2d,
  lottery,
  hardware
}) {
  return [
    questionTool(),
    reminderTool(),
    datetimeTool(),
    systemTool(hardware),
    ...fileTools({ settings, assets, write: writeFile }),
    weatherTool(settings, transport),
    webSearchTool(settings, transport),
    ...live2dTools(live2d),
    lotteryTool(lottery)
  ]
}
