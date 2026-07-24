import { Send, Square } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { IconButton } from './IconButton'

export function MessageComposer({
  value,
  onChange,
  onSend,
  busy,
  onCancel,
  characterName,
}) {
  const textareaRef = useRef(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 96)}px`
  }, [value])

  const submit = (event) => {
    event.preventDefault()
    if (!busy && value.trim()) onSend()
  }

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      if (!busy && value.trim()) onSend()
    }
  }

  return (
    <form className="message-composer" onSubmit={submit}>
      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        placeholder={`发消息给${characterName || '角色'}`}
        aria-label="消息"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      {busy ? (
        <IconButton
          label="停止生成"
          className="composer-action composer-action--stop"
          onClick={onCancel}
        >
          <Square size={19} fill="currentColor" />
        </IconButton>
      ) : (
        <IconButton
          label="发送"
          className="composer-action composer-action--send"
          disabled={!value.trim()}
          onClick={onSend}
        >
          <Send size={20} />
        </IconButton>
      )}
    </form>
  )
}
