import { parameters, syncTool } from './definition.js'

/**
 * 返回带明确时区的当前时间，供提醒计算使用。
 * @param {() => Date} now 可替换的时钟。
 * @returns {object} 时间工具条目。
 */
export function datetimeTool(now = () => new Date()) {
  return syncTool(
    {
      name: 'get_current_datetime',
      description:
        '获取当前本地时间、时区、UTC 标准时间与毫秒时间戳；在计算相对时间或创建定时提醒（create_reminder）前必须先调用。',
      parametersSchema: parameters()
    },
    async () => {
      const date = now()
      return {
        utc: date.toISOString(),
        timestampMs: date.getTime(),
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        local: date.toLocaleString('sv-SE'),
        utcOffsetMinutes: -date.getTimezoneOffset()
      }
    }
  )
}
