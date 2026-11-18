import { useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, Cable, Plus, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Input } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { desktopHost } from '../../host/desktop.js'
import { providerPresets } from '../../../../shared/contracts/llm-providers.js'

const providerOptions = providerPresets.map((item) => ({
  value: item.id,
  label: item.name,
  group: item.id.endsWith('-compatible') ? '自定义协议' : '厂商预设',
  keywords: [item.id]
}))

function makeDraft(connection) {
  return (
    connection ?? {
      id: crypto.randomUUID(),
      revision: 0,
      name: '',
      provider: 'deepseek',
      protocol: 'chat-completions',
      baseUrl: '',
      models: [{ id: providerPresets.find((item) => item.id === 'deepseek').exampleModel }]
    }
  )
}

function modelRows(connection) {
  return (connection.models.length ? connection.models : [{ id: '' }]).map((model) => ({
    key: crypto.randomUUID(),
    model
  }))
}

function ConnectionEditor({ connection, client, onBack, onSaved }) {
  const [draft, setDraft] = useState(() => makeDraft(connection))
  const [rows, setRows] = useState(() => modelRows(draft))
  const [key, setKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [testIndex, setTestIndex] = useState(0)
  const [pending, setPending] = useState(null)
  const [result, setResult] = useState(null)
  const form = useRef(null)
  const preset = providerPresets.find((item) => item.id === draft.provider)
  const fixed =
    preset?.baseUrl &&
    (!connection?.baseUrl ||
      draft.baseUrl === preset.baseUrl ||
      connection.provider !== draft.provider)
  const models = rows.map(({ model }) => model.id.trim())
  const needsKey = Boolean(preset?.baseUrl?.startsWith('https:'))
  const keyReady = !needsKey || key.trim() || connection?.credentialConfigured
  const complete = Boolean(
    (fixed || draft.baseUrl.trim()) && models.length && models.every(Boolean) && keyReady
  )

  function update(field, value) {
    setDraft((current) => ({ ...current, [field]: value }))
    setResult(null)
  }
  function selectProvider(id) {
    const provider = providerPresets.find((item) => item.id === id)
    setDraft((current) => ({
      ...current,
      provider: provider.id,
      protocol: provider.protocol,
      baseUrl: provider.baseUrl,
      behavior: 'auto'
    }))
    setResult(null)
    if (
      !connection &&
      rows.length === 1 &&
      (!rows[0].model.id || rows[0].model.id === preset.exampleModel)
    ) {
      editModel(0, provider.exampleModel ?? '')
    }
  }

  function selectTestModel(value) {
    setTestIndex(Number(value))
  }
  function editModel(index, id) {
    setRows((current) =>
      current.map((row, position) => (position === index ? { ...row, model: { id } } : row))
    )
    setResult(null)
  }
  function addModel() {
    setRows((current) => [...current, { key: crypto.randomUUID(), model: { id: '' } }])
    setResult(null)
  }
  function removeModel(index) {
    setRows((current) => current.filter((_, position) => position !== index))
    setTestIndex(0)
    setResult(null)
  }
  function credentials() {
    if (clearKey) return { kind: 'remove' }
    if (key.trim()) return { kind: 'replace', secret: key.trim() }
    return { kind: 'keep' }
  }
  function definition() {
    return {
      ...draft,
      name: draft.name.trim() || preset.name,
      baseUrl: fixed ? preset.baseUrl : draft.baseUrl.trim(),
      models: rows.map(({ model }, index) => ({
        declared: {},
        overrides: {},
        ...model,
        id: models[index]
      }))
    }
  }
  async function runTest() {
    if (!form.current.reportValidity() || !complete || pending) return
    setPending('test')
    setResult(null)
    try {
      const value = await client.settings.checkConnectionDraft(
        definition(),
        credentials(),
        models[testIndex]
      )
      setResult({
        ok: value.reachable,
        message: value.reachable
          ? '连接测试成功。'
          : (value.problems[0]?.message ?? '连接测试失败。')
      })
    } catch (error) {
      setResult({ ok: false, message: error.message })
    } finally {
      setPending(null)
    }
  }
  async function save(event) {
    event.preventDefault()
    if (!complete || pending) return
    setPending('save')
    setResult(null)
    try {
      await client.settings.saveConnection(definition(), credentials())
      await onSaved()
    } catch (error) {
      setResult({ ok: false, message: error.message })
    } finally {
      setPending(null)
    }
  }
  async function openKeyPage() {
    try {
      await desktopHost().openProviderKeyPage(preset.id)
    } catch (error) {
      setResult({ ok: false, message: error.message })
    }
  }

  return (
    <>
      <Button type="button" size="sm" variant="ghost" onClick={onBack} disabled={Boolean(pending)}>
        <ArrowLeft /> 大模型连接
      </Button>
      <h2>{connection ? '编辑连接' : '新建连接'}</h2>
      <form ref={form} className="settings-form" onSubmit={save}>
        <fieldset disabled={Boolean(pending)}>
          <legend>服务配置</legend>
          <label>
            连接名称
            <Input
              value={draft.name}
              placeholder={preset.name}
              onChange={(event) => update('name', event.target.value)}
            />
          </label>
          <label>
            服务商
            <SelectField
              label="服务商"
              value={draft.provider}
              onChange={selectProvider}
              options={providerOptions}
              disabled={Boolean(pending)}
            />
          </label>
          {!fixed && (
            <label>
              Base URL
              <Input
                required
                type="url"
                value={draft.baseUrl}
                onChange={(event) => update('baseUrl', event.target.value)}
                placeholder="https://…"
              />
            </label>
          )}
          <label>
            API Key
            <Input
              type="password"
              autoComplete="off"
              value={key}
              placeholder={
                connection?.credentialConfigured && !clearKey
                  ? '已配置，留空可保留'
                  : '输入 API Key'
              }
              onChange={(event) => {
                setKey(event.target.value)
                setResult(null)
              }}
            />
          </label>
          <div className="settings-row-actions">
            {preset.keyPage && (
              <Button type="button" size="sm" variant="ghost" onClick={openKeyPage}>
                获取 API Key <ArrowUpRight />
              </Button>
            )}
            {connection?.credentialConfigured && (
              <label className="settings-checkbox">
                <input
                  type="checkbox"
                  checked={clearKey}
                  onChange={(event) => setClearKey(event.target.checked)}
                />
                清除已保存的 Key
              </label>
            )}
          </div>
        </fieldset>
        <fieldset disabled={Boolean(pending)}>
          <legend>模型</legend>
          {rows.map(({ key, model }, index) => (
            <div className="settings-model-field" key={key}>
              <Input
                required
                aria-label={'模型 ID ' + (index + 1)}
                placeholder="模型 ID"
                value={model.id}
                onChange={(event) => editModel(index, event.target.value)}
              />
              {rows.length > 1 && (
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  aria-label={'移除模型 ' + (index + 1)}
                  onClick={() => removeModel(index)}
                >
                  <X />
                </Button>
              )}
            </div>
          ))}
          <Button type="button" size="sm" variant="outline" onClick={addModel}>
            <Plus />
            添加模型
          </Button>
        </fieldset>
        <div className="settings-form-footer">
          {rows.length > 1 && (
            <SelectField
              label="测试模型"
              value={String(testIndex)}
              onChange={selectTestModel}
              options={models.map((model, index) => ({
                value: String(index),
                label: model || '模型 ' + (index + 1)
              }))}
            />
          )}
          <Button
            type="button"
            variant="outline"
            onClick={runTest}
            disabled={!complete || Boolean(pending)}
          >
            {pending === 'test' ? '正在测试…' : '测试连接'}
          </Button>
          <Button type="submit" disabled={!complete || Boolean(pending)}>
            {pending === 'save' ? '正在保存…' : '保存连接'}
          </Button>
        </div>
        {result && (
          <p
            role={result.ok ? 'status' : 'alert'}
            className={result.ok ? 'settings-ok' : 'settings-error'}
          >
            {result.message}
          </p>
        )}
      </form>
    </>
  )
}

/** @param {object} props 连接快照与刷新入口。 @returns {import('react').ReactElement} 管理连接及同层编辑视图。 */
export function Connections({ client, connections, refresh }) {
  const [editor, setEditor] = useState(null)
  const [removing, setRemoving] = useState(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  async function saved() {
    await refresh()
    setEditor(null)
  }
  async function remove(item) {
    setPending(true)
    setError(null)
    try {
      await client.settings.deleteConnection(item.id, item.revision)
      await refresh()
      setRemoving(null)
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  if (editor)
    return (
      <ConnectionEditor
        key={editor.id ?? 'new'}
        client={client}
        connection={editor.id ? editor : null}
        onBack={() => setEditor(null)}
        onSaved={saved}
      />
    )
  return (
    <>
      <div className="settings-group-title">
        <h3>已配置的连接</h3>
        <Button size="sm" onClick={() => setEditor({})}>
          <Plus />
          新建连接
        </Button>
      </div>
      {error && (
        <p className="settings-error" role="alert">
          {error}
        </p>
      )}
      {connections.length === 0 && (
        <div className="settings-card settings-empty">
          <Cable size={22} />
          <div>
            <strong>还没有自有连接</strong>
            <p>添加连接后，可以为每段对话选择使用的模型。</p>
          </div>
        </div>
      )}
      <div className="settings-stack">
        {connections.map((item) => {
          const providerDetailName =
            providerPresets.find((preset) => preset.id === item.provider)?.name ?? item.provider

          return (
            <article className="settings-card" key={item.id}>
              <div className="settings-card-title">
                <div className="settings-connection-summary">
                  <h4>{item.name}</h4>
                  {providerDetailName !== item.name && <p>{providerDetailName}</p>}
                  <ul className="settings-model-tags">
                    {item.models.map((model) => (
                      <li key={model.id}>{model.id}</li>
                    ))}
                  </ul>
                </div>
                <div className="settings-row-actions">
                  <Button size="sm" variant="outline" onClick={() => setEditor(item)}>
                    编辑
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setRemoving(item.id)}>
                    删除
                  </Button>
                </div>
              </div>
              {removing === item.id && (
                <div className="settings-confirm">
                  <p>删除此连接后，使用它的对话需要重新选择模型。</p>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pending}
                    onClick={() => setRemoving(null)}
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={pending}
                    onClick={() => void remove(item)}
                  >
                    删除连接
                  </Button>
                </div>
              )}
            </article>
          )
        })}
      </div>
    </>
  )
}
