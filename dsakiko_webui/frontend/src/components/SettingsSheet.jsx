import { ChevronDown, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { IconButton } from './IconButton'

export function SettingsSheet({ open, busy, onClose, onLoad, onSave }) {
  const [settings, setSettings] = useState(null)
  const [saving, setSaving] = useState(false)
  const [fullscreen, setFullscreen] = useState(Boolean(document.fullscreenElement))

  useEffect(() => {
    if (!open) return
    let active = true
    onLoad().then((result) => {
      if (active) setSettings(result)
    }).catch(() => {})
    return () => { active = false }
  }, [onLoad, open])

  useEffect(() => {
    const syncFullscreen = () => setFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', syncFullscreen)
    return () => document.removeEventListener('fullscreenchange', syncFullscreen)
  }, [])

  if (!open) return null

  const updateVoice = (key, value) => {
    setSettings((current) => ({ ...current, voice: { ...current.voice, [key]: Number(value) } }))
  }

  const save = async () => {
    if (!settings || busy || saving) return
    setSaving(true)
    try {
      const result = await onSave({
        speech_speed: settings.voice.speech_speed,
        sentence_pause_seconds: settings.voice.sentence_pause_seconds,
        llm_choice_id: settings.llm.selected_id,
      })
      setSettings(result)
      onClose()
    } catch {
      // RuntimeProvider presents the shared error toast.
    } finally {
      setSaving(false)
    }
  }

  const toggleFullscreen = async (enabled) => {
    try {
      if (enabled && !document.fullscreenElement) await document.documentElement.requestFullscreen()
      if (!enabled && document.fullscreenElement) await document.exitFullscreen()
    } catch {
      setFullscreen(Boolean(document.fullscreenElement))
    }
  }

  return (
    <div className="settings-sheet-layer" role="presentation" onMouseDown={onClose}>
      <section
        className="settings-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="WebUI 设置"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <span className="sheet-handle" aria-hidden="true" />
        <header className="settings-sheet__header">
          <div><h2>设置</h2><p>{settings?.voice.character_name || '当前角色'}</p></div>
          <IconButton label="关闭设置" onClick={onClose}><X size={20} /></IconButton>
        </header>

        {!settings ? (
          <div className="settings-sheet__loading"><span className="loading-spinner" />正在读取设置</div>
        ) : (
          <div className="settings-sheet__content">
            <section className="settings-group">
              <h3>角色语音</h3>
              <label className="setting-slider">
                <span><b>语速</b><output>{settings.voice.speech_speed.toFixed(2)}</output></span>
                <input type="range" min="0.6" max="1.4" step="0.01" value={settings.voice.speech_speed} onChange={(event) => updateVoice('speech_speed', event.target.value)} />
              </label>
              <label className="setting-slider">
                <span><b>句间停顿</b><output>{settings.voice.sentence_pause_seconds.toFixed(2)} 秒</output></span>
                <input type="range" min="0.1" max="0.8" step="0.01" value={settings.voice.sentence_pause_seconds} onChange={(event) => updateVoice('sentence_pause_seconds', event.target.value)} />
              </label>
            </section>

            <section className="settings-group">
              <h3>大模型配置</h3>
              <label className="model-select">
                <div>
                  <select value={settings.llm.selected_id} onChange={(event) => setSettings((current) => ({ ...current, llm: { ...current.llm, selected_id: event.target.value } }))}>
                    {settings.llm.options.map((option) => (
                      <option key={option.id} value={option.id}>{option.label} · {option.model}</option>
                    ))}
                  </select>
                  <ChevronDown size={18} aria-hidden="true" />
                </div>
              </label>
              <p className="settings-hint">API Key 或自定义请求地址需在电脑端配置。</p>
            </section>

            <section className="settings-group">
              <h3>显示</h3>
              <label className="fullscreen-setting">
                <span><b>全屏模式</b><small>隐藏浏览器UI</small></span>
                <input
                  type="checkbox"
                  checked={fullscreen}
                  disabled={!document.fullscreenEnabled}
                  onChange={(event) => toggleFullscreen(event.target.checked)}
                />
              </label>
            </section>
          </div>
        )}

        <button type="button" className="primary-command settings-save" disabled={!settings || busy || saving} onClick={save}>
          {busy ? '回复完成后可保存' : (saving ? '正在保存' : '保存设置')}
        </button>
      </section>
    </div>
  )
}
