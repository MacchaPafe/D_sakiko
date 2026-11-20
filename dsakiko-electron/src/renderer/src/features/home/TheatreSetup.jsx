import { useEffect, useId, useState } from 'react'
import { ChevronRight, Play, SlidersHorizontal, Square, X } from 'lucide-react'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { Button } from '../../components/ui/button.jsx'
import { Input, Textarea } from '../../components/ui/fields.jsx'
import { CharacterAvatar } from './CharacterAvatar.jsx'
import { characterFormName } from './character-form.js'

function Choice({ options, value, label, disabled, onChange }) {
  if (options.length === 1 || disabled) {
    return (
      <strong className="theatre-choice-static">
        {options.find((item) => item.value === value)?.label}
      </strong>
    )
  }
  if (options.length < 4) {
    return (
      <div className="theatre-choice-group" role="group" aria-label={label}>
        {options.map((item) => (
          <Button
            key={item.value}
            variant="ghost"
            type="button"
            size="sm"
            aria-pressed={value === item.value}
            onClick={() => onChange(item.value)}
          >
            {item.label}
          </Button>
        ))}
      </div>
    )
  }
  return <SelectField options={options} value={value} label={label} onChange={onChange} />
}

function hasAnyCharacterDetail(characterNotes) {
  return Object.values(characterNotes ?? {}).some((entry) =>
    Boolean(entry?.speakingStyle?.trim() || entry?.interaction?.trim())
  )
}

function countFilledDetails(ids, details) {
  return ids.reduce((total, id) => {
    const entry = details[id]
    return (
      total +
      Number(Boolean(entry?.speakingStyle?.trim())) +
      Number(Boolean(entry?.interaction?.trim()))
    )
  }, 0)
}

/**
 * 双人小剧场的创建与设定浮窗，支持选角、形态切换、情境及可选的角色表现细节。
 * @param {object} props 当前剧场或新建入口及角色目录。
 * @returns {import('react').ReactElement} 三段式固定头尾布局的小剧场设定表单。
 */
export function TheatreSetup({ client, flow, roles, chat, close }) {
  const [ids, setIds] = useState(
    () => chat?.characterIds ?? roles.slice(0, 2).map((role) => role.id)
  )
  const [characters, setCharacters] = useState(chat?.meta.settings.characters ?? {})
  const [details, setDetails] = useState(chat?.meta.settings.scenario?.characters ?? {})
  const [situation, setSituation] = useState(chat?.meta.settings.scenario?.situation ?? '')
  const [detailsOpen, setDetailsOpen] = useState(() =>
    hasAnyCharacterDetail(chat?.meta.settings.scenario?.characters)
  )
  const [definitions, setDefinitions] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const detailsId = useId()

  useEffect(() => {
    let current = true
    Promise.all(ids.map((id) => client.characters.get(id)))
      .then((items) => {
        if (current) setDefinitions(Object.fromEntries(items.map((item) => [item.id, item])))
      })
      .catch((failure) => {
        if (current) setError(failure.message)
      })
    return () => {
      current = false
    }
  }, [ids, client])

  function choose(index, id) {
    setIds((old) =>
      old.map((value, position) => {
        if (position === index) return id
        return value === id ? old[index] : value
      })
    )
  }

  function changeForm(id, formId) {
    setCharacters((old) => ({ ...old, [id]: { ...old[id], formId } }))
  }

  function updateDetail(id, key, value) {
    setDetails((old) => ({ ...old, [id]: { ...old[id], [key]: value } }))
  }

  function toggleDetails() {
    setDetailsOpen((open) => !open)
  }

  async function save(event) {
    event.preventDefault()
    if (busy || ids.length !== 2) return
    setBusy(true)
    setError('')
    try {
      const selections = {}
      const notes = {}
      for (const id of ids) {
        selections[id] = { formId: characters[id]?.formId ?? definitions[id].defaultFormId }
        notes[id] = { speakingStyle: '', interaction: '', ...details[id] }
      }
      const settings = {
        characters: selections,
        scenario: { situation: situation.trim(), characters: notes }
      }
      if (chat) await client.conversations.patchSettings(chat.id, settings)
      else
        await flow.createTheatre(
          ids,
          settings,
          ids
            .map((id) => definitions[id].name ?? roles.find((role) => role.id === id).displayName)
            .join(' · ')
        )
      close()
    } catch (failure) {
      setError(failure.message)
      setBusy(false)
    }
  }

  const roleOptions = roles.map((role) => ({
    value: role.id,
    label: role.name ?? role.displayName,
    keywords: [role.id, role.displayName, role.bangDreamId ?? '']
  }))
  const filledDetailCount = countFilledDetails(ids, details)

  function renderActorCard(id, index) {
    const role = roles.find((item) => item.id === id)
    const definition = definitions[id]
    const actorName = definition?.name ?? role?.displayName ?? id
    const sideBadge = index === 0 ? '左侧' : '右侧'
    const sideLabel = index === 0 ? '左侧演员' : '右侧演员'
    const formOptions = (definition?.forms ?? []).map((form) => ({
      value: form.id,
      label: characterFormName(definition, form)
    }))

    return (
      <article className="theatre-actor-card" key={index}>
        <div className="theatre-actor-identity">
          <CharacterAvatar
            role={definition ?? role}
            client={client}
            size={36}
            className="theatre-actor-avatar"
          />
          <div className="theatre-actor-title">
            <span className="theatre-side-badge">{sideBadge}</span>
            <strong className="theatre-actor-name">{actorName}</strong>
          </div>
        </div>
        {!chat && (
          <div className="theatre-field">
            <span className="theatre-field-label">选择角色</span>
            <Choice
              label={sideLabel}
              options={roleOptions}
              value={id}
              disabled={busy}
              onChange={(value) => choose(index, value)}
            />
          </div>
        )}
        {formOptions.length > 1 && (
          <div className="theatre-field">
            <span className="theatre-field-label">角色形态</span>
            <Choice
              label={sideLabel + '形态'}
              options={formOptions}
              value={characters[id]?.formId ?? definition.defaultFormId}
              disabled={busy}
              onChange={(formId) => changeForm(id, formId)}
            />
          </div>
        )}
      </article>
    )
  }

  function renderDetailCard(id, index) {
    const role = roles.find((item) => item.id === id)
    const definition = definitions[id]
    const actorName = definition?.name ?? role?.displayName ?? id
    return (
      <div key={index} className="theatre-detail-card">
        <div className="theatre-detail-actor">
          <CharacterAvatar
            role={definition ?? role}
            client={client}
            size={22}
            className="theatre-detail-avatar"
          />
          <span>{actorName}</span>
        </div>
        <label className="theatre-field">
          <span className="theatre-field-label">说话风格</span>
          <Input
            value={details[id]?.speakingStyle ?? ''}
            disabled={busy}
            placeholder="例如：语气轻快、爱接话"
            onChange={(event) => updateDetail(id, 'speakingStyle', event.target.value)}
          />
        </label>
        <label className="theatre-field">
          <span className="theatre-field-label">互动细节</span>
          <Input
            value={details[id]?.interaction ?? ''}
            disabled={busy}
            placeholder="例如：嘴硬但其实很在意对方"
            onChange={(event) => updateDetail(id, 'interaction', event.target.value)}
          />
        </label>
      </div>
    )
  }

  return (
    <form className="theatre-form" onSubmit={save} data-dialog-busy={busy || undefined}>
      <header className="theatre-form-header">
        <div className="theatre-form-heading">
          <h2 tabIndex={-1} data-dialog-focus>
            {chat ? '剧场设定' : '新建小剧场'}
          </h2>
          <p className="theatre-form-subtitle">
            {chat ? '调整本场的情境与角色表现要求' : '选择两位同台演员，并可补充情境与表现要求'}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="theatre-form-close"
          aria-label="关闭剧场设定"
          onClick={close}
          disabled={busy}
        >
          <X size={18} />
        </Button>
      </header>
      <div className="theatre-form-body">
        <section className="theatre-cast-grid" aria-label="参演角色">
          {ids.map(renderActorCard)}
        </section>
        <label className="theatre-field theatre-situation-field">
          <span className="theatre-field-label">情境（可选）</span>
          <Textarea
            rows={2}
            value={situation}
            disabled={busy}
            onChange={(event) => setSituation(event.target.value)}
            placeholder="例如，排练结束后，两人在门口等雨停。"
          />
        </label>
        <section className="theatre-details-section">
          <button
            type="button"
            className="theatre-details-toggle"
            aria-expanded={detailsOpen}
            aria-controls={detailsId}
            onClick={toggleDetails}
          >
            <ChevronRight size={16} className="theatre-details-chevron" aria-hidden="true" />
            <span className="theatre-details-title">角色细节（可选）</span>
            <span className="theatre-details-summary">
              {filledDetailCount > 0 ? `已填 ${filledDetailCount} 项` : '说话风格与互动微调'}
            </span>
          </button>
          {detailsOpen && (
            <div id={detailsId} className="theatre-details-grid">
              {ids.map(renderDetailCard)}
            </div>
          )}
        </section>
        {ids.length < 2 && <p className="theatre-form-error">需要两位不同的角色才能创建小剧场。</p>}
      </div>
      {error && (
        <p className="theatre-form-error theatre-form-alert" role="alert">
          {error}
        </p>
      )}
      <footer className="theatre-form-footer">
        <Button type="button" variant="outline" onClick={close} disabled={busy}>
          取消
        </Button>
        <Button
          type="submit"
          disabled={busy || ids.length !== 2 || ids.some((id) => !definitions[id])}
        >
          {chat ? '保存设定' : '创建小剧场'}
        </Button>
      </footer>
    </form>
  )
}

/**
 * 渲染舞台底部的小剧场操作条，包含设定、回放、模型选择及整场生成/停止控制。
 * @param {object} props 剧场快照、回放状态、模型选择器及生成控制回调。
 * @returns {import('react').ReactElement} 毛玻璃材质的小剧场底部控制栏。
 */
export function TheatreActions({
  snapshot,
  sending,
  problem,
  confirming,
  canReplay,
  replaying,
  canGenerate,
  modelControl,
  cancel,
  configure,
  replay,
  stop,
  generate
}) {
  const active = Boolean(snapshot.activeTurnId)
  const hasTurns = snapshot.chat.turns.length > 0
  const issue = problem ?? snapshot.executions.at(-1)?.problem?.message
  const generateLabel = hasTurns ? '重新生成本场' : '生成本场'
  const replayLabel = replaying ? '停止回放' : '回放本场'

  return (
    <div className="theatre-actions-group">
      {issue && (
        <div className="composer-problem" role="alert">
          {issue}
        </div>
      )}
      {confirming && !active && (
        <div className="theatre-confirm-bar" role="status">
          <span>生成成功后将替换当前剧本，是否继续？</span>
          <div className="theatre-confirm-actions">
            <Button size="sm" variant="ghost" onClick={cancel}>
              取消
            </Button>
            <Button size="sm" disabled={sending || !canGenerate} onClick={generate}>
              确认重新生成
            </Button>
          </div>
        </div>
      )}
      <div className="theatre-actions">
        <div className="theatre-actions-leading">
          <Button
            size="sm"
            variant="ghost"
            className="theatre-action-btn"
            disabled={active || sending}
            onClick={configure}
          >
            <SlidersHorizontal size={14} aria-hidden="true" />
            剧场设定
          </Button>
          {!active && (canReplay || replaying) && (
            <Button
              size="sm"
              variant="ghost"
              className="theatre-action-btn"
              disabled={sending}
              onClick={replay}
            >
              {replaying ? (
                <Square size={14} aria-hidden="true" />
              ) : (
                <Play size={14} aria-hidden="true" />
              )}
              {replayLabel}
            </Button>
          )}
          {active && (
            <span className="theatre-status-text" role="status">
              {snapshot.activity === 'generating' ? '正在生成剧本…' : '正在演出…'}
            </span>
          )}
        </div>
        <div className="theatre-actions-trailing">
          {modelControl}
          {active ? (
            <Button size="sm" onClick={stop}>
              <Square size={14} aria-hidden="true" />
              停止本场
            </Button>
          ) : (
            <Button size="sm" disabled={sending || confirming || !canGenerate} onClick={generate}>
              {generateLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
