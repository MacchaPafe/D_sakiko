/** 会话工具及其中文名称。分组只用于展示，存档使用稳定的工具名。 */
export const toolGroups = [
  [
    '文件操作',
    [
      ['list_directory', '浏览目录'],
      ['read_file_content', '读取文件'],
      ['grep_search_in_file', '搜索文件内容'],
      ['export_document', '导出文档']
    ]
  ],
  [
    '网页访问',
    [
      ['web_search', '搜索网页'],
      ['read_url', '读取网页']
    ]
  ],
  [
    '时间与提醒',
    [
      ['get_current_datetime', '获取日期与时间'],
      ['create_reminder', '设置提醒']
    ]
  ],
  [
    '角色换装',
    [
      ['fetch_all_live2d_models', '查看服装'],
      ['change_character_live2d', '切换服装']
    ]
  ],
  [
    '互动',
    [
      ['ask_user', '向我提问'],
      ['start_lottery', '随机抽签']
    ]
  ],
  ['查询天气', [['get_weather', '查询天气']]],
  ['查看硬件状态', [['get_system_hardware_status', '查看硬件状态']]]
]
const prerequisites = {
  create_reminder: 'get_current_datetime',
  change_character_live2d: 'fetch_all_live2d_models'
}
export const lotteryDurationMs = 2000

/** @param {string} mode 对话模式。 @returns {string[]} 新会话逐项选择。 */
export function defaultToolNames(mode) {
  if (mode === 'scripted-dialogue') return ['read_file_content', 'grep_search_in_file', 'read_url']
  return toolGroups.flatMap(([, items]) => items.map(([name]) => name))
}

/** @param {string[]} current 当前选择。 @param {string[]} names 被切换的项。 @returns {string[]} 联动前置工具后的新选择。 */
export function toggleTools(current, names) {
  const selected = new Set(current)
  const enable = !names.every((name) => selected.has(name))
  for (const name of names) {
    if (enable) {
      selected.add(name)
      if (prerequisites[name]) selected.add(prerequisites[name])
    } else {
      selected.delete(name)
      for (const [dependent, required] of Object.entries(prerequisites))
        if (required === name) selected.delete(dependent)
    }
  }
  return [...selected].sort()
}

/** @param {object} connection 已解析连接。 @returns {boolean} 是否使用官方 DeepSeek 对话协议。 */
export function officialDeepSeekChat(connection) {
  return (
    connection?.provider === 'deepseek' &&
    ['chat-completions', 'chat', 'openai-compatible'].includes(
      connection.protocol ?? 'chat-completions'
    ) &&
    [
      'https://api.deepseek.com',
      'https://api.deepseek.com/v1',
      'https://api.deepseek.com/beta'
    ].includes((connection.baseUrl || 'https://api.deepseek.com/v1').replace(/\/+$/, ''))
  )
}
/** @param {object} connection 模型连接。 @returns {boolean} 当前是否支持服务端联网和标题。 */
export function officialFlash(connection) {
  return officialDeepSeekChat(connection) && connection.modelId === 'deepseek-flash'
}

/** 检查工具替换后实际并存的联网和本地调用。 */
export function webConflict(connection, names, reasoning) {
  return (
    officialFlash(connection) &&
    reasoning?.enabled === false &&
    names.some((name) => ['read_url', 'web_search'].includes(name)) &&
    names.some((name) => !['read_url', 'web_search', 'get_current_datetime'].includes(name))
  )
}
