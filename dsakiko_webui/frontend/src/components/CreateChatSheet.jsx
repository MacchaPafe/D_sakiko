import { ChevronDown, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Avatar } from './Avatar'
import { IconButton } from './IconButton'

function PersonaAvatar({ persona }) {
  const label = persona?.is_default ? '无' : (persona?.name || '我').slice(0, 1)
  return <span className="persona-avatar" aria-hidden="true">{label}</span>
}

export function CreateChatSheet({
  open,
  busy,
  characters,
  userPersonas,
  onClose,
  onCreate,
}) {
  const [characterId, setCharacterId] = useState('')
  const [personaId, setPersonaId] = useState('')
  const [name, setName] = useState('')

  const selectedCharacter = useMemo(
    () => characters.find((item) => item.id === characterId) || characters[0],
    [characterId, characters],
  )
  const selectedPersona = useMemo(
    () => (
      userPersonas.find((item) => item.id === personaId)
      || userPersonas.find((item) => item.is_default)
      || userPersonas[0]
    ),
    [personaId, userPersonas],
  )

  if (!open) return null

  const submit = async (event) => {
    event.preventDefault()
    if (!selectedCharacter || busy) return
    const created = await onCreate({
      characterId: selectedCharacter.id,
      name,
      userPersonaId: selectedPersona?.id || null,
    })
    if (created) {
      setName('')
      onClose()
    }
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
        <span className="sheet-handle" aria-hidden="true" />
        <header>
          <h2 id="create-chat-title">新建对话</h2>
          <IconButton label="关闭" onClick={onClose}>
            <X size={19} />
          </IconButton>
        </header>

        <form onSubmit={submit}>
          <fieldset disabled={busy}>
            <label className="field-label" htmlFor="new-chat-character">选择角色</label>
            <div className="select-control">
              <Avatar character={selectedCharacter} size="select" />
              <strong>{selectedCharacter?.name || '没有可用角色'}</strong>
              <ChevronDown size={18} aria-hidden="true" />
              <select
                id="new-chat-character"
                value={selectedCharacter?.id || ''}
                disabled={characters.length === 0}
                onChange={(event) => setCharacterId(event.target.value)}
              >
                {characters.map((character) => (
                  <option key={character.id} value={character.id}>{character.name}</option>
                ))}
              </select>
            </div>

            <label className="field-label" htmlFor="new-chat-name">
              <span>对话名称</span>
              <small>可选</small>
            </label>
            <input
              id="new-chat-name"
              className="text-field"
              value={name}
              maxLength={80}
              placeholder="例如：练习后的闲聊"
              onChange={(event) => setName(event.target.value)}
            />

            <label className="field-label" htmlFor="new-chat-persona">
              <span>你的身份</span>
              <small className="immutable-hint">创建后不可更改</small>
            </label>
            <div className="select-control select-control--persona">
              <PersonaAvatar persona={selectedPersona} />
              <strong>{selectedPersona?.name || '默认身份'}</strong>
              <ChevronDown size={18} aria-hidden="true" />
              <select
                id="new-chat-persona"
                value={selectedPersona?.id || ''}
                disabled={userPersonas.length === 0}
                onChange={(event) => setPersonaId(event.target.value)}
              >
                {userPersonas.map((persona) => (
                  <option key={persona.id} value={persona.id}>{persona.name}</option>
                ))}
              </select>
            </div>

            <div className="persona-summary">
              <small>hint: 可在电脑端的角色设置中管理身份</small>
            </div>
          </fieldset>

          <button
            className="primary-command create-chat-command"
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
