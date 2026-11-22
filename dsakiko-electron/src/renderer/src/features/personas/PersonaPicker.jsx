import { useEffect, useState } from 'react'
import { UserRound } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Choice } from '../../components/ui/Choice.jsx'
import { CharacterAvatar } from '../home/CharacterAvatar.jsx'
import { personaOptions } from './source.js'
import './personas.css'

/** @param {object} props 当前草稿、会话和打开查看器的回调。 @returns {import('react').ReactElement|null} 输入区工具栏的纯图标身份入口。 */
export function IdentityButton({ state, flow, onOpen }) {
  const { chatId, characterId } = state.target
  if (!chatId && !characterId) return null
  if (chatId && !state.snapshot?.chat.userPersona) return null
  const persona = chatId
    ? state.snapshot?.chat.userPersona
    : state.personas.find((item) => item.id === flow.input().userPersonaId)
  const problem = Boolean(persona?.problem)
  const active = Boolean(persona && !problem)
  const name = problem ? '来源不可用' : persona?.displayName || '无身份'
  const label = '我的身份 · ' + name
  const title = chatId ? '当前对话身份：' + name + '（已锁定）' : '我的身份：' + name
  return (
    <Button
      className="persona-trigger"
      variant="ghost"
      size="icon-sm"
      onClick={onOpen}
      aria-label={label}
      title={title}
      data-active={active || undefined}
      data-problem={problem || undefined}
      disabled={!chatId && state.sending}
    >
      {active && persona.avatar ? (
        <CharacterAvatar role={persona} client={flow.client} size={20} />
      ) : (
        <UserRound size={16} aria-hidden="true" />
      )}
    </Button>
  )
}

/** @param {object} props 当前有效身份或聊天快照、资源客户端与展示模式。 @returns {import('react').ReactElement} 用户身份预览卡片。 */
export function PersonaPreview({ persona, client, compact = false, subtitle = null }) {
  const previewClass = compact ? 'persona-preview persona-preview-compact' : 'persona-preview'
  return (
    <div className={previewClass}>
      {persona?.avatar ? (
        <CharacterAvatar role={persona} client={client} size={44} />
      ) : (
        <span className="persona-avatar-fallback" aria-hidden="true">
          <UserRound size={20} />
        </span>
      )}
      <div className="persona-preview-body">
        <div className="persona-preview-title">
          <strong>{persona ? persona.displayName || '未命名身份' : '无身份'}</strong>
          {subtitle && <span className="persona-preview-badge">{subtitle}</span>}
        </div>
        {persona && <p>{persona.description || '未填写身份描述'}</p>}
      </div>
    </div>
  )
}

/** @param {object} props 当前草稿或已保存的聊天、身份目录和浮窗导航。 @returns {import('react').ReactElement} 草稿选择器或只读身份快照。 */
export function PersonaPicker({ flow, state, onManage, onClose }) {
  const { client } = flow
  const frozen = Boolean(state.target.chatId)
  const [candidate, setCandidate] = useState(flow.input().userPersonaId ?? '')
  const [resolved, setResolved] = useState(null)
  const item = state.personas.find((entry) => entry.id === candidate)
  const selected = item?.id ?? ''
  useEffect(() => {
    let live = true
    if (item && !frozen && !item.problem) {
      client.characters.resolvePersona(item.id).then(
        (persona) => {
          if (live) setResolved({ item, persona })
        },
        (error) => {
          if (live) setResolved({ item, error: error.message })
        }
      )
    }
    return () => {
      live = false
    }
  }, [client, item, frozen])
  const preview = frozen
    ? state.snapshot?.chat.userPersona
    : resolved?.item === item && resolved?.persona
  const problem =
    !frozen &&
    (state.personaProblem || item?.problem?.message || (resolved?.item === item && resolved?.error))
  const role = state.characters.find((entry) => entry.id === state.target.characterId)
  const currentForm = state.draftOutfit?.formId ?? role?.defaultFormId
  const otherNames = [
    role?.name ?? role?.displayName,
    role?.forms?.find((form) => form.id === currentForm)?.displayName
  ]
  const options = [
    { value: '', label: '无身份' },
    ...personaOptions(state.personas).filter((option) => !option.disabled)
  ]
  function confirm() {
    flow.choosePersona(selected || null)
    onClose()
  }
  function manage() {
    setCandidate(flow.input().userPersonaId ?? '')
    onManage()
  }
  function viewIdentity() {
    onManage(preview.personaId)
  }
  return (
    <section className="persona-picker">
      <h2 data-dialog-focus tabIndex={-1}>
        我的身份
      </h2>
      {!frozen && options.length > 1 && (
        <Choice label="选择对话身份" value={selected} options={options} onChange={setCandidate} />
      )}
      {!frozen && options.length === 1 && selected && (
        <Button variant="outline" onClick={() => setCandidate('')}>
          选择无身份
        </Button>
      )}
      {frozen && (
        <div className="persona-picker-snapshot" role="region" aria-label="对话身份详情" tabIndex={0}>
          <PersonaPreview client={client} persona={preview} />
        </div>
      )}
      {!frozen && (preview || !selected) && (
        <PersonaPreview client={client} persona={preview} compact />
      )}
      {!frozen && selected && !preview && !problem && <p role="status">正在读取身份…</p>}
      {!frozen && <p className="persona-picker-hint">对话开始后，将无法修改自己的身份。</p>}
      {!frozen && preview && otherNames.includes(preview.displayName) && (
        <p role="status">你的身份与对话角色同名，回复中可能混淆双方。</p>
      )}
      {problem && (
        <p role="alert">
          {problem}{' '}
          <Button variant="ghost" size="sm" onClick={flow.refreshPersonas}>
            重试
          </Button>
        </p>
      )}
      <footer>
        {frozen && preview && (
          <Button variant="outline" onClick={viewIdentity}>
            前往查看
          </Button>
        )}
        {!frozen && (
          <Button variant="outline" onClick={manage}>
            管理身份
          </Button>
        )}
        <Button variant="ghost" onClick={onClose}>
          {frozen ? '关闭' : '取消'}
        </Button>
        {!frozen && (
          <Button onClick={confirm} disabled={Boolean(selected && (!preview || problem))}>
            确认选择
          </Button>
        )}
      </footer>
    </section>
  )
}
