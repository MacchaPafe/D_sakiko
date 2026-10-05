import { useState } from 'react'
import { Check, ChevronRight, Circle, Shirt, LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { runtimeClient as client } from '@/runtime/client'

/**
 * 展示后端发现的共享模型；草稿选择只有在应用成功后才进入角色设置。
 * @param {object} props 角色清单、初始角色、运行状态与更新回调。
 * @returns {import('react').ReactElement} 角色装扮选择面板。
 */
export function CharacterModelsForm({ characters, initialId, snapshots, onSaved, onClose }) {
  const [characterId, setCharacterId] = useState(initialId || characters[0]?.id)
  const [drafts, setDrafts] = useState({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const character = characters.find((item) => item.id === characterId)
  const selectedId = drafts[characterId] ?? character?.selectedModelId
  const selected = character?.models.find((item) => item.id === selectedId)
  const busy = snapshots.some(
    (item) => item.chat.characterIds.includes(characterId) && item.activity !== 'idle'
  )
  const changed = selectedId !== character?.selectedModelId

  function selectCharacter(event) {
    setCharacterId(event.currentTarget.dataset.id)
    setError('')
  }
  function selectModel(event) {
    setDrafts((previous) => ({ ...previous, [characterId]: event.target.value }))
    setError('')
  }
  async function apply(event) {
    event.preventDefault()
    if (!changed || busy || saving || !selected || selected.problem) return
    setSaving(true)
    setError('')
    try {
      const updated = await client.setCharacterModel(characterId, selectedId)
      onSaved(updated)
      onClose()
    } catch (error) {
      setError(error.message)
    } finally {
      setSaving(false)
    }
  }
  function renderCharacter(item) {
    const current = item.models.find((model) => model.id === item.selectedModelId)
    return (
      <button
        type="button"
        className="wardrobe-character"
        key={item.id}
        data-id={item.id}
        aria-pressed={item.id === characterId}
        disabled={saving}
        onClick={selectCharacter}
      >
        <span className="wardrobe-monogram">{item.displayName.slice(0, 1)}</span>
        <span>
          <strong>{item.displayName}</strong>
          <small>{current?.label || '尚无模型'}</small>
        </span>
        <ChevronRight size={13} />
      </button>
    )
  }
  function renderModel(model) {
    const chosen = model.id === selectedId
    return (
      <label
        key={model.id}
        className={`outfit-option ${chosen ? 'chosen' : ''} ${model.problem ? 'unavailable' : ''}`}
      >
        <input
          type="radio"
          name="character-model"
          value={model.id}
          checked={chosen}
          disabled={Boolean(model.problem) || saving}
          onChange={selectModel}
          aria-label={model.label}
        />
        <span className="outfit-icon">
          <Shirt size={19} strokeWidth={1.4} />
        </span>
        <span className="outfit-description">
          <span className="outfit-title">
            <strong>{model.label}</strong>
            {model.version && <span className="model-version">{model.version.toUpperCase()}</span>}
            {model.id === character.selectedModelId && <small>使用中</small>}
          </span>
          <span className="outfit-path">{model.relativePath}</span>
          {model.problem && <span className="inline-error">{model.problem}</span>}
        </span>
        {chosen ? (
          <Check className="outfit-check" size={16} />
        ) : (
          <Circle className="outfit-check" size={16} />
        )}
      </label>
    )
  }
  return (
    <form className="wardrobe" onSubmit={apply}>
      <div className="wardrobe-intro">
        <Shirt size={19} strokeWidth={1.4} />
        <div>
          <p>为下一次相遇，换一套装扮。</p>
          <small>使用共用资源中的模型，应用到该角色的所有对话。</small>
        </div>
      </div>
      {characters.length > 0 ? (
        <div className="wardrobe-body">
          <nav className="wardrobe-characters" aria-label="选择装扮角色">
            {characters.map(renderCharacter)}
          </nav>
          <section className="wardrobe-outfits" aria-label="可用装扮">
            <div className="wardrobe-heading">
              <h3>{character?.displayName}</h3>
              <span>{character?.models.length || 0} 套装扮</span>
            </div>
            <div className="outfit-list">{character?.models.map(renderModel)}</div>
            {!character?.models.length && (
              <p className="wardrobe-empty">暂未找到模型。请检查设置中的共用资源目录。</p>
            )}
          </section>
        </div>
      ) : (
        <p className="wardrobe-empty">尚未发现角色，请先在设置中选择共用资源目录。</p>
      )}
      <footer className="wardrobe-footer">
        <div role="status">
          {error && <p className="inline-error">{error}</p>}
          {busy ? (
            <p className="wardrobe-busy">该角色正在生成或演出，结束后即可换装。</p>
          ) : (
            <p className="form-hint">只记住模型选择，不修改共用资源或旧程序配置。</p>
          )}
        </div>
        <Button
          type="submit"
          disabled={!changed || busy || saving || !selected || Boolean(selected.problem)}
        >
          {saving ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />}
          {saving ? '正在应用…' : '应用装扮'}
        </Button>
      </footer>
    </form>
  )
}
