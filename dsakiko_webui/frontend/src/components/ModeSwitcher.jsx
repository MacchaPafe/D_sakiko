import { MessageCircle, Sparkles } from 'lucide-react'

export function ModeSwitcher({ value, onChange }) {
  return (
    <div className="mode-switcher" aria-label="显示模式">
      <button
        type="button"
        className={value === 'character' ? 'is-active' : ''}
        aria-pressed={value === 'character'}
        onClick={() => onChange('character')}
      >
        <Sparkles size={16} />
        <span>角色</span>
      </button>
      <button
        type="button"
        className={value === 'chat' ? 'is-active' : ''}
        aria-pressed={value === 'chat'}
        onClick={() => onChange('chat')}
      >
        <MessageCircle size={16} />
        <span>消息</span>
      </button>
    </div>
  )
}
