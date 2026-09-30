import { useEffect, useRef, useState } from 'react'
import { Avatar } from './Avatar'

export function AvatarMenu({ character, onChange }) {
  const [open, setOpen] = useState(false)
  const root = useRef(null)
  const trigger = useRef(null)
  const item = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    item.current?.focus()
    const dismiss = (event) => {
      if (!root.current?.contains(event.target)) setOpen(false)
    }
    const escape = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setOpen(false)
        trigger.current?.focus()
      }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  return (
    <div ref={root} className="message-avatar-menu" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
    }}>
      <button ref={trigger} type="button" className="message-avatar-trigger"
        aria-label={`${character?.name || '角色'}的头像`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((value) => !value)}>
        <Avatar character={character} size="message" />
      </button>
      {open && <div className="avatar-popover" role="menu">
        <button ref={item} type="button" role="menuitem" onClick={() => {
          setOpen(false)
          trigger.current?.focus()
          onChange()
        }}>更改头像</button>
      </div>}
    </div>
  )
}
