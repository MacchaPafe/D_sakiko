import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { stageModelKey } from '../../performance/stage-layout.js'

/**
 * @param {object} props 当前角色、对话选择及舞台加载状态。
 * @returns {import('react').ReactElement|null} 服装列表，仅展示当前形态的模型。
 */
export function StageWardrobe({
  open,
  panelRef,
  id,
  role,
  client,
  chatSelection,
  draftOutfit,
  stageState,
  savingDraft,
  onChoose,
  onRetryModel,
  onClose
}) {
  const [catalog, setCatalog] = useState(null)
  const [retry, setRetry] = useState(0)
  const [savingId, setSavingId] = useState(null)
  const [saveError, setSaveError] = useState(null)

  useEffect(() => {
    if (!open) return
    let current = true
    void client.characters.get(role.id).then(
      (definition) => {
        if (current) setCatalog({ role, retry, definition })
      },
      (error) => {
        if (current) setCatalog({ role, retry, error: error.message })
      }
    )
    return () => {
      current = false
    }
  }, [open, client, role, retry])

  if (!open) return null
  const result = catalog?.role === role && catalog.retry === retry ? catalog : null
  const definition = result?.definition
  const formId = chatSelection?.formId ?? draftOutfit?.formId ?? definition?.defaultFormId
  const form = definition?.forms.find((item) => item.id === formId)
  const models = form?.models ?? []
  const selectedId =
    chatSelection?.modelOverrides?.[formId] ?? draftOutfit?.model.id ?? form?.defaultModelId
  const selected = models.find((item) => item.id === selectedId)
  const shown = stageState.shownOutfitKey
  const current = shown
    ? (models.find((item) => item === selected && stageModelKey(item.presentation) === shown) ??
      models.find((item) => stageModelKey(item.presentation) === shown))
    : null

  async function choose(model) {
    if (savingDraft || savingId !== null) return
    setSaveError(null)
    if (model === selected) {
      if (stageState.failedOutfitKey === stageModelKey(model.presentation)) onRetryModel()
      return
    }
    setSavingId(model.id)
    try {
      await onChoose(form.id, model, form.defaultModelId)
    } catch (error) {
      setSaveError({ id: model.id, message: error.message })
    } finally {
      setSavingId(null)
    }
  }

  function retryCatalog() {
    setRetry((value) => value + 1)
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
          <span>{model.name || model.id}</span>
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
          <h2 id={id + '-title'}>服装</h2>
          <p>{definition?.name || role.displayName}</p>
        </div>
        <Button variant="ghost" size="icon" aria-label="收起服装" onClick={onClose}>
          <X size={17} />
        </Button>
      </header>
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
        {result && !result.error && !models.length && <p>当前形态暂无服装</p>}
        {models.map(renderModel)}
      </div>
      <footer>
        <Button variant="ghost" size="sm" disabled>
          获取更多服装 <span>即将开放</span>
        </Button>
      </footer>
    </section>
  )
}
