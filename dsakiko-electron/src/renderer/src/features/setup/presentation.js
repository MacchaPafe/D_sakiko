/** @param {number|null|undefined} bytes 后端字节数。 @param {number|null} fractionDigits 可选的固定小数位数，用于稳定总量显示。 @returns {string} 未知大小不显示为零。 */
export function formatBytes(bytes, fractionDigits = null) {
  if (bytes == null || !Number.isFinite(bytes)) return '大小待确认'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index =
    bytes === 0 ? 2 : Math.min(4, Math.max(0, Math.floor(Math.log(bytes) / Math.log(1000))))
  const number = (bytes / 1000 ** index).toLocaleString('zh-CN', {
    minimumFractionDigits: fractionDigits ?? 0,
    maximumFractionDigits: fractionDigits ?? (index >= 3 ? 2 : 1)
  })
  return `${number} ${units[index]}`
}

/** @param {number|null} seconds 后端估计，不能用动画计时伪造。 @returns {string|null} 可选的剩余时间说明。 */
export function remainingTime(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return null
  if (seconds < 60) return '预计不到 1 分钟'
  return `预计还需约 ${Math.ceil(seconds / 60)} 分钟`
}

const labels = {
  waiting: '等待下载',
  planned: '等待开始',
  running: '准备中',
  downloading: '下载中',
  installing: '安装与检查中',
  validating: '校验中',
  committing: '正在启用',
  ready: '已就绪',
  paused: '已暂停',
  failed: '准备失败',
  cancelled: '已取消',
  interrupted: '等待继续',
  complete: '准备完成',
  succeeded: '准备完成'
}
/** @param {string} status 后端任务阶段。 @returns {string} 不暴露内部阶段代号的显示文案。 */
export function statusLabel(status) {
  return labels[status] ?? '准备中'
}

/** @param {import('./setup-flow.js').SetupSnapshot} job 任务。 @returns {Array<{id: string, name: string, status: string}>} 将运行环境与基础模型合并展示，只有全部组件就绪才算完成一项。 */
export function taskRows(job) {
  const groups = new Map()
  for (const component of job.download?.components ?? []) {
    const id = component.id.startsWith('gsv.') ? 'gsv' : component.id
    if (!groups.has(id)) groups.set(id, [])
    groups.get(id).push(component)
  }
  const names = {
    gsv: 'GPT-SoVITS',
    'voice.sakiko': '祥子',
    'voice.anon': '爱音',
    'voice.soyo': '素世',
    'asr.runtime': '语音识别环境',
    'asr.models': '语音转文字'
  }
  return [...groups].map(([id, components]) => {
    let status = 'waiting'
    if (components.every((item) => item.status === 'ready')) status = 'ready'
    else {
      const active = components.find((item) => !['ready', 'waiting'].includes(item.status))
      if (active) status = active.status
    }
    return { id, name: names[id] ?? '资源组件', status }
  })
}
