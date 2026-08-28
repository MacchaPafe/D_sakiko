import { Check, RefreshCw, Shirt, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { IconButton } from '../components/IconButton'

export function Live2DModelSheet({
  open,
  presentationTargetId,
  runtimeState,
  onClose,
  onLoad,
  onSelect,
}) {
  const [catalog, setCatalog] = useState(null)
  const [loading, setLoading] = useState(false)
  const [selectingId, setSelectingId] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setCatalog(await onLoad())
    } catch (loadError) {
      setError(loadError.message || '服装列表读取失败。')
    } finally {
      setLoading(false)
    }
  }, [onLoad])

  useEffect(() => {
    if (!open) return undefined
    const timer = window.setTimeout(load, 0)
    return () => window.clearTimeout(timer)
  }, [load, open, presentationTargetId])

  if (!open) return null

  const requestClose = () => {
    if (!selectingId) onClose()
  }

  const select = async (option) => {
    if (selectingId || !option.available || option.is_current) return
    setSelectingId(option.option_id)
    setError('')
    try {
      await onSelect(option.option_id)
      onClose()
    } catch (selectError) {
      setError(selectError.message || '服装切换失败。')
      if (selectError.code === 'LIVE2D_OPTIONS_STALE') await load()
    } finally {
      setSelectingId(null)
    }
  }

  return (
    <div className="live2d-model-sheet-layer" role="presentation" onMouseDown={requestClose}>
      <section
        className="live2d-model-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="选择角色服装"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <span className="sheet-handle" aria-hidden="true" />
        <header className="live2d-model-sheet__header">
          <div>
            <span className="live2d-model-sheet__eyebrow"><Shirt size={14} /> LIVE2D WARDROBE</span>
            <h2>选择服装</h2>
            <p>{catalog?.character_name || '当前角色'}</p>
          </div>
          <div className="live2d-model-sheet__tools">
            <IconButton label="刷新服装列表" onClick={load} disabled={loading || Boolean(selectingId)}>
              <RefreshCw size={19} className={loading ? 'is-spinning' : ''} />
            </IconButton>
            <IconButton label="关闭服装列表" onClick={requestClose} disabled={Boolean(selectingId)}>
              <X size={20} />
            </IconButton>
          </div>
        </header>

        {error && <p className="live2d-model-sheet__error" role="alert">{error}</p>}

        {loading && !catalog ? (
          <div className="live2d-model-sheet__empty" role="status">
            <span className="loading-spinner" aria-hidden="true" />正在整理服装
          </div>
        ) : !catalog?.supported ? (
          <div className="live2d-model-sheet__empty">
            <Shirt size={28} aria-hidden="true" />
            <p>{catalog?.message || '该角色暂不支持手动选择服装。'}</p>
          </div>
        ) : catalog.options.length === 0 ? (
          <div className="live2d-model-sheet__empty">
            <Shirt size={28} aria-hidden="true" />
            <p>还没有找到可用服装。</p>
          </div>
        ) : (
          <div className="live2d-model-list" aria-busy={Boolean(selectingId)}>
            {catalog.options.map((option) => {
              const renderFailed = option.is_current && runtimeState?.status === 'error'
              const status = renderFailed
                ? '加载失败'
                : option.is_current
                  ? '当前服装'
                  : option.error || ''
              return (
                <button
                  type="button"
                  className={`live2d-model-option ${option.is_current ? 'is-current' : ''}`}
                  key={option.option_id}
                  disabled={Boolean(selectingId) || !option.available || option.is_current}
                  onClick={() => select(option)}
                >
                  <span className="live2d-model-option__name">{option.name}</span>
                  {selectingId === option.option_id ? (
                    <span className="loading-spinner" aria-label="正在切换" />
                  ) : option.is_current ? (
                    <Check size={18} aria-hidden="true" />
                  ) : null}
                  {status && <small className={renderFailed ? 'is-error' : ''}>{status}</small>}
                </button>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
