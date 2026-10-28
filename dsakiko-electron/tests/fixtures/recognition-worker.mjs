import process from 'node:process'
import { setTimeout } from 'node:timers'
// 使用真实子进程验证 IPC 与取消；不在自动测试中加载大型模型。
process.on('disconnect', () => process.exit(0))
process.on('message', (request) => {
  if (request.language === 'crash') process.exit(1)
  process.send({ id: request.id, kind: 'state', state: 'transcribing' })
  if (request.language === 'wait') return
  setTimeout(() => {
    process.send({ id: 'stale-response', result: { text: '迟到的旧文本' } })
    process.send({ id: request.id, result: { text: '本次识别结果' } })
  }, 20)
})
