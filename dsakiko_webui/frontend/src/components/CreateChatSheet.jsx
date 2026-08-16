import { Check, X } from 'lucide-react'
import { useState } from 'react'
import { Avatar } from './Avatar'
import { IconButton } from './IconButton'

export function CreateChatSheet({
  open,
  busy,
  characters,
  userPersonas,
  onClose,
  onCreate,
}) {
  const [characterId, setCharacterId] = useState('')
  const [name, setName] = useState('')

  if (!open) return null

  const submit = async (event) => {
    event.preventDefault()
    const selectedCharacterId = characterId || characters[0]?.id
    if (!selectedCharacterId) return
    const created = await onCreate({
      characterId: selectedCharacterId,
      name,
      userPersonaId: userPersonas[0]?.id || null,
    })
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
            <p className="eyebrow">会话</p>
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
              {characters.map((character, index) => {
                const selected = characterId
                  ? characterId === character.id
                  : index === 0
                return (
                  <label
                    key={character.id}
                    className={selected ? 'is-selected' : ''}
                    style={{ '--option-accent': character.accent }}
                  >
                    <input
                      type="radio"
                      name="character"
                      value={character.id}
                      checked={selected}
                      onChange={() => setCharacterId(character.id)}
                    />
                    <Avatar character={character} size="small" />
                    <span>{character.name}</span>
                    {selected && <Check size={17} />}
                  </label>
                )
              })}
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

          <button
            className="primary-command"
            type="submit"
            disabled={busy || characters.length === 0}
          >
            创建并进入
          </button>
        </form>
      </section>
    </div>
  )
}
