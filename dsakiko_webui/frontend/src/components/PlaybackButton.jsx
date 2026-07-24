import { Pause, Play, Volume2 } from 'lucide-react'
import { IconButton } from './IconButton'

export function PlaybackButton({ message, playback, onToggle }) {
  if (!message.audio_url) return null
  const isCurrent = playback.messageId === message.id
  const isPlaying = isCurrent && playback.status === 'playing'

  return (
    <span className="playback-control">
      <IconButton
        label={isPlaying ? '暂停语音' : '播放语音'}
        className={isCurrent ? 'is-current' : ''}
        onClick={(event) => {
          event.stopPropagation()
          onToggle(message)
        }}
      >
        {isPlaying ? <Pause size={16} /> : (
          isCurrent ? <Volume2 size={16} /> : <Play size={16} fill="currentColor" />
        )}
      </IconButton>
      {isCurrent && (
        <span className="playback-progress" aria-hidden="true">
          <span style={{ width: `${Math.round(playback.progress * 100)}%` }} />
        </span>
      )}
    </span>
  )
}
