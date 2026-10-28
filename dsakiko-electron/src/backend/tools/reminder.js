import { parameters, syncTool, textParameter } from './definition.js'

/** 保留已有提醒工具名称，补齐时间查询依赖。 */
export function reminderTool() {
  return syncTool(
    {
      name: 'create_reminder',
      description:
        '创建到期后触发角色主动开口的定时提醒；调用前必须先通过 get_current_datetime 获取当前时间与时区。',
      dependencies: ['get_current_datetime'],
      requiredCapabilities: ['reminders'],
      parametersSchema: parameters(
        {
          dueAt: {
            ...textParameter,
            description:
              '提醒到期的 ISO 8601 时间字符串（必须包含时区偏移或 Z，例如 "2026-10-28T20:00:00+08:00"）。'
          },
          instruction: {
            ...textParameter,
            description:
              '到期触发时提供给角色自身的行动指令（写明届时要主动对对方说什么或提醒什么事项）。'
          }
        },
        ['dueAt', 'instruction']
      )
    },
    ({ reminders }, input, source) => reminders.create(input, source)
  )
}
