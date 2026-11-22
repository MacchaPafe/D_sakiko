import { characterFormName } from './character-form.js'
import { useEffect, useState } from 'react'
import { Check, ChevronRight, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Choice } from '../../components/ui/Choice.jsx'
import { stageModelKey } from '../../performance/stage-layout.js'

function wardrobeSubtitle(definition, role, form, theatreMode) {
  const characterName = definition?.name || role.name || role.displayName
  const hasMultipleForms = Boolean(definition && definition.forms.length > 1 && form)
  if (theatreMode) return hasMultipleForms ? characterFormName(definition, form) : null
  return characterName
}

/**
 * @param {object} props 当前角色、对话选择及舞台加载状态。
 * @returns {import('react').ReactElement|null} 换装与单人对话人格切换浮窗。
 */
export function StageWardrobe({
  open,
  panelRef,
  id,
  role,
  client,
  chatSelection,
  draftOutfit,
  hasChat,
  personaBusy,
  onChooseForm,
  stageState,
  savingDraft,
  participants,
  onActor,
  onChoose,
  onManage,
  onRetryModel,
  onClose
}) {
  const [catalog, setCatalog] = useState(null)
  const [retry, setRetry] = useState(0)
  const [savingId, setSavingId] = useState(null)
  const [saveError, setSaveError] = useState(null)
  const [switchingForm, setSwitchingForm] = useState(false)
  const [formError, setFormError] = useState(null)
  const [switchHintSeq, setSwitchHintSeq] = useState(0)

  useEffect(() => {
    if (!open) return
    let current = true
    void client.characters.get(role.id).then(
      (definition) => {
        if (current) setCatalog({ roleId: role.id, retry, definition })
      },
      (error) => {
        if (current) setCatalog({ roleId: role.id, retry, error: error.message })
      }
    )
    return () => {
      current = false
    }
  }, [open, client, role, retry])

  useEffect(() => {
    if (!switchHintSeq) return
    const timer = setTimeout(() => setSwitchHintSeq(0), open ? 4500 : 0)
    return () => clearTimeout(timer)
  }, [open, switchHintSeq])

  useEffect(() => {
    if (open || !formError) return
    const timer = setTimeout(() => setFormError(null), 0)
    return () => clearTimeout(timer)
  }, [open, formError])

  if (!open) return null
  const result = catalog?.roleId === role.id && catalog.retry === retry ? catalog : null
  const definition = result?.definition
  const forms = definition?.forms ?? role?.forms ?? []
  const hasMultipleForms = Boolean(!participants && forms.length > 1)
  const formId =
    chatSelection?.formId ?? draftOutfit?.formId ?? definition?.defaultFormId ?? role?.defaultFormId
  const form = definition?.forms.find((item) => item.id === formId)
  const models = form?.models ?? []
  const selectedId =
    chatSelection?.modelOverrides?.[formId] ?? draftOutfit?.model?.id ?? form?.defaultModelId
  const selected = models.find((item) => item.id === selectedId)
  const shown = stageState.shownOutfitKey
  const current = shown
    ? (models.find((item) => item === selected && stageModelKey(item.presentation) === shown) ??
      models.find((item) => stageModelKey(item.presentation) === shown))
    : null

  async function handleChooseForm(nextFormId) {
    if (!onChooseForm || switchingForm || personaBusy || nextFormId === formId) return
    setFormError(null)
    setSwitchingForm(true)
    try {
      await onChooseForm(nextFormId)
      if (hasChat) setSwitchHintSeq((value) => value + 1)
    } catch (error) {
      setFormError(error.message)
    } finally {
      setSwitchingForm(false)
    }
  }

  async function choose(model) {
    if (savingDraft || savingId !== null) return
    setSaveError(null)
    if (model === selected) {
      if (stageState.failedOutfitKey === stageModelKey(model.presentation)) onRetryModel()
      return
    }
    setSavingId(model.id)
    try {
      await onChoose(form.id, model)
    } catch (error) {
      setSaveError({ id: model.id, message: error.message })
    } finally {
      setSavingId(null)
    }
  }

  function retryCatalog() {
    setRetry((value) => value + 1)
  }

  function manage() {
    onManage(formId ?? role.defaultFormId)
  }

  function renderModel(model) {
    const active = current?.id === model.id
    const failed =
      model === selected && stageState.failedOutfitKey === stageModelKey(model.presentation)
    const changing = savingId === model.id || (model === selected && !active && !failed)
    const error = saveError?.id === model.id ? saveError.message : null
    return (
      <div className="stage-outfit" key={model.id}>
        <Button
          variant="ghost"
          className="stage-outfit-option"
          aria-pressed={active}
          aria-disabled={savingDraft || savingId !== null || (!failed && model === selected)}
          onClick={() => void choose(model)}
        >
          <span className="stage-outfit-name">
            <span>{model.name || model.id}</span>
            {model.version && (
              <span className="stage-outfit-version">{model.version.toUpperCase()}</span>
            )}
          </span>
          {active && (
            <span className="stage-outfit-current">
              <Check size={14} />
              当前
            </span>
          )}
          {changing && (
            <span className="stage-outfit-status" role="status">
              加载中…
            </span>
          )}
          {failed && <span className="stage-outfit-status">重试</span>}
        </Button>
        {failed && (
          <p className="home-inline-problem" role="alert">
            服装加载失败
          </p>
        )}
        {error && (
          <p className="home-inline-problem" role="alert">
            {error}
          </p>
        )}
      </div>
    )
  }

  const subtitle = wardrobeSubtitle(definition, role, form, Boolean(participants))

  return (
    <section
      className="stage-wardrobe"
      ref={panelRef}
      id={id}
      role="dialog"
      aria-labelledby={id + '-title'}
    >
      <header className="stage-wardrobe-heading">
        <div>
          <h2 id={id + '-title'}>{hasMultipleForms ? '人格与服装' : '服装'}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <Button variant="ghost" size="icon" aria-label="收起服装" onClick={onClose}>
          <X size={17} />
        </Button>
      </header>
      {participants && (
        <div className="theatre-actor-tabs" role="group" aria-label="选择换装演员">
          {participants.map((actor) => (
            <Button
              variant="ghost"
              size="sm"
              key={actor.id}
              aria-pressed={actor.id === role.id}
              onClick={() => onActor(actor.id)}
            >
              {actor.name ?? actor.displayName}
            </Button>
          ))}
        </div>
      )}
      {hasMultipleForms && (
        <div className="stage-wardrobe-persona">
          <div className="stage-wardrobe-persona-row">
            <span className="stage-wardrobe-section-label">当前人格</span>
            <Choice
              label="当前人格"
              value={formId}
              onChange={handleChooseForm}
              disabled={Boolean(personaBusy)}
              options={forms.map((item) => ({ value: item.id, label: item.displayName }))}
            />
          </div>
          {formError && (
            <p className="home-inline-problem" role="alert">
              {formError}
            </p>
          )}
          {hasChat && switchHintSeq > 0 && (
            <p className="stage-wardrobe-hint" role="status">
              对话中切换人格可能导致身份混淆与缓存率下降
            </p>
          )}
        </div>
      )}
      <div className="stage-wardrobe-list">
        {!result && <p role="status">正在读取服装…</p>}
        {result?.error && (
          <>
            <p className="home-inline-problem" role="alert">
              {result.error}
            </p>
            <Button variant="ghost" size="sm" onClick={retryCatalog}>
              重试
            </Button>
          </>
        )}
        {result && !result.error && !models.length && <p>当前人格暂无服装</p>}
        {models.map(renderModel)}
      </div>
      <footer>
        <Button variant="ghost" size="sm" onClick={manage}>
          管理服装
          <ChevronRight size={14} aria-hidden="true" />
        </Button>
      </footer>
    </section>
  )
}
