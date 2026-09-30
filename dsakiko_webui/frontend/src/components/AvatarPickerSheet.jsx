import { X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { IconButton } from './IconButton'

export function AvatarPickerSheet({ character, onClose, onLoad, onSave }) {
  const [catalog, setCatalog] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const dialog = useRef(null)
  const closeButton = useRef(null)
  useEffect(() => {
    let active = true
    onLoad(character.id).then((result) => {
      if (!active) return
      setCatalog(result.avatar)
      setSelected(result.avatar.selected_id)
    }).catch((reason) => {
      if (active) setError(reason.message || '头像读取失败，请重试。')
    })
    return () => { active = false }
  }, [character.id, onLoad, attempt])

  useEffect(() => {
    const previous = document.activeElement
    closeButton.current?.querySelector('button')?.focus()
    return () => { if (previous?.isConnected) previous.focus() }
  }, [])

  const close = () => { if (!saving) onClose() }
  const save = async () => {
    if (!selected || saving) return
    setSaving(true)
    setError('')
    try {
      await onSave({ avatar: { character_id: character.id, avatar_id: selected } })
      onClose()
    } catch (reason) {
      setError(reason.message || '头像保存失败，请重试。')
    } finally {
      setSaving(false)
    }
  }
  const keyDown = (event) => {
    if (event.key === 'Escape') { event.stopPropagation(); close() }
    if (event.key !== 'Tab') return
    const buttons = [...dialog.current.querySelectorAll('button:not(:disabled)')]
    const first = buttons[0]
    const last = buttons[buttons.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
  return (
    <div className="settings-sheet-layer avatar-sheet-layer" role="presentation" onMouseDown={close}>
      <section ref={dialog} className="settings-sheet avatar-sheet" role="dialog" aria-modal="true"
        aria-label={`更改${character.name}的头像`} onMouseDown={(event) => event.stopPropagation()} onKeyDown={keyDown}>
        <span className="sheet-handle" aria-hidden="true" />
        <header className="settings-sheet__header">
          <h2>更改头像</h2>
          <span ref={closeButton}><IconButton label="关闭头像选择" disabled={saving} onClick={close}><X size={20} /></IconButton></span>
        </header>
        <p className="avatar-sheet__hint">可在 Live2D 模型下载器中为角色添加新头像</p>
        <div className="avatar-sheet__content">
          {!catalog && !error && <div className="settings-sheet__loading" role="status"><span className="loading-spinner" />正在读取头像</div>}
          {catalog && <div className="avatar-choice-grid" role="group" aria-label="可用头像">
            {catalog.options.map((option) => (
              <button key={option.id} type="button" className={`avatar-choice ${selected === option.id ? 'is-selected' : ''}`}
                aria-label={option.name} aria-pressed={selected === option.id} title={option.name} disabled={saving}
                onClick={() => setSelected(option.id)}>
                <img src={option.image_url} alt="" />
              </button>
            ))}
          </div>}
          {catalog?.options.length === 0 && <p className="avatar-sheet__empty">暂无可用头像，请先通过下载器下载。</p>}
          {error && <div className="avatar-sheet__error" role="alert">
            <p>{error}</p><button type="button" disabled={saving} onClick={() => {
              setCatalog(null); setSelected(null); setError(''); setAttempt((value) => value + 1)
            }}>重新读取</button>
          </div>}
        </div>
        <button type="button" className="primary-command settings-save" disabled={!selected || saving || !catalog} onClick={save}>
          {saving ? '正在保存' : '确定'}
        </button>
      </section>
    </div>
  )
}
