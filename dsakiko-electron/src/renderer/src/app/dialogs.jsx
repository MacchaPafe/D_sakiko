import { useEffect, useRef, useState } from 'react'
import { X, FolderOpen, UserRound, Check, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Textarea } from '@/components/ui/fields'

/**
 * 原生模态框提供焦点约束和 Escape 行为。
 * @param {object} props 标题、内容与关闭回调。
 * @returns {import('react').ReactElement} 模态框。
 */
export function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef(null)
  useEffect(() => {
    ref.current.showModal()
  }, [])
  function cancel(event) {
    event.preventDefault()
    onClose()
  }
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? 'modal-wide' : ''}`}
      onCancel={cancel}
      aria-label={title}
    >
      <header className="modal-header">
        <h2>{title}</h2>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭">
          <X size={18} />
        </Button>
      </header>
      {children}
    </dialog>
  )
}

/**
 * 创建一人或二人聊天，身份快照由后端在受理时固定。
 * @param {object} props 角色、人格及创建回调。
 * @returns {import('react').ReactElement} 新建聊天内容。
 */
export function NewChatForm({ characters, personas, onCreate, onClose, Avatar }) {
  const [selected, setSelected] = useState([])
  const [persona, setPersona] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  function choose(event) {
    const id = event.currentTarget.dataset.id
    setSelected((previous) => {
      if (previous.includes(id)) return previous.filter((value) => value !== id)
      if (previous.length < 2) return [...previous, id]
      return previous
    })
  }
  async function submit(event) {
    event.preventDefault()
    setPending(true)
    try {
      await onCreate({ characterIds: selected, userPersonaId: persona || null })
      onClose()
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  function renderCharacter(character) {
    return (
      <button
        type="button"
        key={character.id}
        className={`character-option ${selected.includes(character.id) ? 'chosen' : ''}`}
        data-id={character.id}
        onClick={choose}
        aria-pressed={selected.includes(character.id)}
      >
        <Avatar asset={character.avatar} name={character.displayName} />
        <span>
          <strong>{character.displayName}</strong>
          <small>
            {character.hasModel ? 'Live2D' : '文字演出'} ·{' '}
            {character.hasVoice ? '声音已就绪' : '无语音'}
          </small>
        </span>
        <Check className="choice-mark" size={17} />
      </button>
    )
  }
  return (
    <form onSubmit={submit} className="form-stack">
      <p className="muted">选择一名角色聊天，或让两名角色一起登场。</p>
      <div className="character-grid">{characters.map(renderCharacter)}</div>
      {!characters.length && (
        <p className="inline-error">没有可用角色。请先在设置中选择资源目录。</p>
      )}
      <label className="field-label">
        我在对话中的身份
        <select value={persona} onChange={(event) => setPersona(event.target.value)}>
          <option value="">默认身份</option>
          {personas.map((item) => (
            <option key={item.id} value={item.id} disabled={Boolean(item.problem)}>
              {item.displayName || '来源角色不可用'}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      <footer className="form-footer">
        <span className="muted">
          {selected.length === 2 ? <Users size={15} /> : <UserRound size={15} />}
          {selected.length === 2 ? '二人对话' : '单角色对话'}
        </span>
        <Button type="submit" disabled={!selected.length || pending}>
          {pending ? '正在创建…' : '开始对话'}
        </Button>
      </footer>
    </form>
  )
}

/**
 * 人格编辑支持自定义文字和关联已有角色。
 * @param {object} props 目录、客户端与关闭回调。
 * @returns {import('react').ReactElement} 人格编辑内容。
 */
export function PersonaForm({ personas, characters, client, onSaved }) {
  const [definition, setDefinition] = useState({
    revision: 0,
    source: { kind: 'custom', displayName: '', description: '', avatar: null }
  })
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  async function choose(event) {
    const id = event.target.value
    try {
      setDefinition(
        id
          ? await client.getPersona(id)
          : {
              revision: 0,
              source: { kind: 'custom', displayName: '', description: '', avatar: null }
            }
      )
      setError('')
    } catch (error) {
      setError(error.message)
    }
  }
  function changeKind(event) {
    const kind = event.target.value
    setDefinition({
      ...definition,
      source:
        kind === 'custom'
          ? { kind, displayName: '', description: '', avatar: null }
          : { kind, characterId: characters[0]?.id || '' }
    })
  }
  function changeField(event) {
    setDefinition({
      ...definition,
      source: { ...definition.source, [event.target.name]: event.target.value }
    })
  }
  async function save(event) {
    event.preventDefault()
    setPending(true)
    try {
      const saved = await client.savePersona(definition)
      setDefinition(saved)
      await onSaved()
      setError('')
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  const source = definition.source
  return (
    <form className="form-stack" onSubmit={save}>
      <label className="field-label">
        编辑人格
        <select value={definition.id || ''} onChange={choose}>
          <option value="">新建人格</option>
          {personas.map((item) => (
            <option key={item.id} value={item.id}>
              {item.displayName || '来源角色不可用'}
            </option>
          ))}
        </select>
      </label>
      <div className="segmented">
        <label>
          <input
            type="radio"
            value="custom"
            checked={source.kind === 'custom'}
            onChange={changeKind}
          />
          自定义身份
        </label>
        <label>
          <input
            type="radio"
            value="character"
            checked={source.kind === 'character'}
            onChange={changeKind}
          />
          扮演现有角色
        </label>
      </div>
      {source.kind === 'custom' ? (
        <>
          <label className="field-label">
            名称
            <Input
              name="displayName"
              value={source.displayName}
              onChange={changeField}
              required
              maxLength={100}
              placeholder="角色会如何称呼你"
            />
          </label>
          <label className="field-label">
            描述
            <Textarea
              name="description"
              value={source.description}
              onChange={changeField}
              rows={6}
              placeholder="你的背景、性格，以及与角色的关系…"
            />
          </label>
        </>
      ) : (
        <label className="field-label">
          角色
          <select name="characterId" value={source.characterId} onChange={changeField}>
            {characters.map((item) => (
              <option key={item.id} value={item.id}>
                {item.displayName}
              </option>
            ))}
          </select>
        </label>
      )}
      <p className="form-hint">保存只影响之后新建的聊天。已有聊天会保留创建时的身份。</p>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      <footer className="form-footer">
        <span />
        <Button type="submit" disabled={pending}>
          {pending ? '正在保存…' : '保存人格'}
        </Button>
      </footer>
    </form>
  )
}

/**
 * 明确区分模型连接与本机语音资源，不显示已经保存的凭据。
 * @param {object} props 设置快照、客户端与保存回调。
 * @returns {import('react').ReactElement} 设置内容。
 */
export function SettingsForm({ initial, client, onSaved }) {
  const [values, setValues] = useState(initial)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  function change(event) {
    const { name, type, value, checked } = event.target
    let next = value
    if (type === 'checkbox') next = checked
    if (type === 'number') next = Number(value)
    setValues((previous) => ({ ...previous, [name]: next }))
  }
  async function chooseDirectory() {
    const path = await client.chooseResourceRoot()
    if (path) setValues((previous) => ({ ...previous, resourceRoot: path }))
  }
  async function choosePython() {
    const path = await client.choosePython()
    if (path) setValues((previous) => ({ ...previous, pythonExecutable: path }))
  }
  async function save(event) {
    event.preventDefault()
    setPending(true)
    setError('')
    setMessage('')
    try {
      setValues(await client.saveSettings(values))
      await onSaved()
      setMessage('设置已保存')
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  async function importLegacy() {
    setPending(true)
    setError('')
    setMessage('')
    try {
      await client.saveSettings(values)
      setValues(await client.importLegacySettings())
      await onSaved()
      setMessage('已读取模型连接与设备设置')
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  return (
    <form className="form-stack" onSubmit={save}>
      <div className="settings-section">
        <h3>模型连接</h3>
        <p className="form-hint">使用兼容 Chat Completions 的模型接口。</p>
        <label className="field-label">
          服务地址
          <Input
            name="baseUrl"
            value={values.baseUrl}
            onChange={change}
            placeholder="https://…/v1"
          />
        </label>
        <div className="field-row">
          <label className="field-label">
            模型名称
            <Input name="model" value={values.model} onChange={change} />
          </label>
          <label className="field-label">
            API 密钥
            <Input
              type="password"
              name="apiKey"
              value={values.apiKey}
              onChange={change}
              placeholder={values.hasApiKey ? '已保存，留空保持不变' : '填写密钥'}
              autoComplete="off"
            />
          </label>
        </div>
        <div className="field-row">
          <label className="field-label">
            台词语言
            <select name="language" value={values.language} onChange={change}>
              <option value="ja">日语 · 中文翻译</option>
              <option value="zh">中文</option>
            </select>
          </label>
          <label className="field-label">
            温度
            <Input
              type="number"
              name="temperature"
              min="0"
              max="2"
              step="0.1"
              value={values.temperature}
              onChange={change}
            />
          </label>
        </div>
      </div>
      <div className="settings-section">
        <h3>本机资源</h3>
        <p className="form-hint">
          选择包含 live2d_related 和 reference_audio 的旧程序目录。原资源只读。
        </p>
        <label className="field-label">
          资源根目录
          <div className="path-field">
            <Input name="resourceRoot" value={values.resourceRoot} onChange={change} />
            <Button
              type="button"
              variant="outline"
              onClick={chooseDirectory}
              aria-label="选择资源目录"
            >
              <FolderOpen size={16} />
            </Button>
          </div>
        </label>
        <Button
          type="button"
          variant="outline"
          onClick={importLegacy}
          disabled={pending || !values.resourceRoot}
        >
          从旧配置读取模型连接
        </Button>
        <label className="field-label">
          Python 解释器
          <div className="path-field">
            <Input name="pythonExecutable" value={values.pythonExecutable} onChange={change} />
            <Button
              type="button"
              variant="outline"
              onClick={choosePython}
              aria-label="选择 Python 解释器"
            >
              <FolderOpen size={16} />
            </Button>
          </div>
        </label>
      </div>
      <div className="settings-section">
        <h3>声音</h3>
        <label className="toggle-label">
          <input
            type="checkbox"
            name="voiceEnabled"
            checked={values.voiceEnabled}
            onChange={change}
          />
          合成并播放角色声音
        </label>
        <div className="field-row three">
          <label className="field-label">
            设备
            <select name="device" value={values.device} onChange={change}>
              <option value="cpu">CPU</option>
              <option value="mps">Apple MPS</option>
              <option value="cuda">NVIDIA CUDA</option>
            </select>
          </label>
          <label className="field-label">
            同时推理数
            <Input
              type="number"
              name="maxConcurrent"
              min="1"
              max="4"
              value={values.maxConcurrent}
              onChange={change}
            />
          </label>
          <label className="field-label">
            常驻模型数
            <Input
              type="number"
              name="maxResident"
              min="1"
              max="4"
              value={values.maxResident}
              onChange={change}
            />
          </label>
        </div>
        <p className="form-hint">
          更多常驻模型会占用更多内存。修改设备或预算会重启语音进程，需要先结束正在进行的对话。
        </p>
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      <footer className="form-footer">
        <span role="status" className="success-note">
          {message}
        </span>
        <Button type="submit" disabled={pending}>
          {pending ? '正在保存…' : '保存设置'}
        </Button>
      </footer>
    </form>
  )
}
