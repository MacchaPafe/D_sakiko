import { Wifi, WifiOff } from 'lucide-react'

export function RuntimeIndicator({ connection, phase = 'idle', compact = false }) {
  const online = connection === 'ready'
  const phaseText = {
    thinking: '思考中',
    tts: '生成语音',
    idle: '已连接',
  }[phase] || '已连接'

  return (
    <span
      className={`runtime-indicator ${online ? 'is-online' : 'is-offline'} ${compact ? 'is-compact' : ''}`}
      aria-label={online ? phaseText : '连接中断'}
    >
      {online ? <Wifi size={14} /> : <WifiOff size={14} />}
      {!compact && <span>{online ? phaseText : '连接中断'}</span>}
    </span>
  )
}
