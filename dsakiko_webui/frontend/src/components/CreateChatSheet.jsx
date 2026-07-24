import { Check, X } from 'lucide-react'
import { useState } from 'react'
import { MOCK_CHARACTERS } from '../runtime/mockData'
import { Avatar } from './Avatar'
import { IconButton } from './IconButton'

const CHARACTERS = Object.values(MOCK_CHARACTERS)

export function CreateChatSheet({ open, busy, onClose, onCreate }) {
  const [characterId, setCharacterId] = useState(CHARACTERS[0].id)
  const [name, setName] = useState('')

  if (!open) return null

  const submit = (event) => {
    event.preventDefault()
    const created = onCreate({ characterId, name })
    if (created) onClose()
  }

  return (
    <div className="sheet-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="create-chat-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-chat-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <p className="eyebrow">NEW CHAT</p>
            <h2 id="create-chat-title">新建会话</h2>
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>

        <form onSubmit={submit}>
          <fieldset disabled={busy}>
            <legend>选择角色</legend>
            <div className="character-options">
              {CHARACTERS.map((character) => (
                <label
                  key={character.id}
                  className={characterId === character.id ? 'is-selected' : ''}
                  style={{ '--option-accent': character.accent }}
                >
                  <input
                    type="radio"
                    name="character"
                    value={character.id}
                    checked={characterId === character.id}
                    onChange={() => setCharacterId(character.id)}
                  />
                  <Avatar character={character} size="small" />
                  <span>{character.name}</span>
                  {characterId === character.id && <Check size={17} />}
                </label>
              ))}
            </div>
          </fieldset>

          <label className="field-label">
            <span>会话名称</span>
            <input
              value={name}
              maxLength={40}
              placeholder="可选"
              onChange={(event) => setName(event.target.value)}
            />
          </label>

          <button className="primary-command" type="submit" disabled={busy}>
            创建并进入
          </button>
        </form>
      </section>
    </div>
  )
}
