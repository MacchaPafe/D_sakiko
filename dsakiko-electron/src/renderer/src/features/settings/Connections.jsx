import { useId, useRef, useState } from 'react'
import {
  ArrowLeft,
  ArrowUpRight,
  Brain,
  Cable,
  Check,
  ChevronDown,
  GripVertical,
  Plus,
  X
} from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { Input } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { orderItems, sameOrder, useSortableRows } from '../../hooks/use-sortable-rows.js'
import '../../components/ui/sortable-rows.css'
import { desktopHost } from '../../host/desktop.js'
import { providerPresets } from '../../../../shared/contracts/llm-providers.js'

import { ChatGPTAccountFields, VertexAccountFields } from './ManagedAccountFields.jsx'

const managedProviders = ['vertex', 'chatgpt']
const emptyVertex = { project: '', email: '', location: 'global' }
const reasoningStatuses = [
  { value: 'auto', label: '自动识别' },
  { value: 'supported', label: '支持思考' },
  { value: 'unsupported', label: '不支持思考' }
]
const reasoningEffortOptions = [
  { value: 'minimal', label: '最低' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
  { value: 'xhigh', label: '超高' },
  { value: 'max', label: '最高' }
]
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
  const clearKeyId = useId()
  const [draft, setDraft] = useState(() => makeDraft(connection))
  const [rows, setRows] = useState(() => modelRows(draft))
  const [key, setKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [vertex, setVertex] = useState(connection?.vertex ?? emptyVertex)
  const [ticket, setTicket] = useState(null)
  const [account, setAccount] = useState(null)
  const isVertex = draft.provider === 'vertex'
  const isChatGPT = draft.provider === 'chatgpt'
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
  let keyReady = !needsKey || key.trim() || connection?.credentialConfigured
  if (isVertex)
    keyReady =
      vertex.project &&
      vertex.email &&
      vertex.location &&
      (ticket || key.trim() || connection?.credentialConfigured)
  if (isChatGPT)
    keyReady = account?.connected && models.every((id) => account.models.some((m) => m.id === id))
  const complete = Boolean(
    (fixed || draft.baseUrl.trim()) && models.length && models.every(Boolean) && keyReady
  )

  function update(field, value) {
    setDraft((current) => ({ ...current, [field]: value }))
    setResult(null)
  }
  function selectProvider(id) {
    const provider = providerPresets.find((item) => item.id === id)
    if (managedProviders.includes(id) || managedProviders.includes(draft.provider)) {
      setRows(modelRows({ models: [{ id: provider.exampleModel ?? '' }] }))
      setTestIndex(0)
      setAccount(null)
      setTicket(null)
      setVertex(emptyVertex)
      setKey('')
      setClearKey(false)
    }
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
      !managedProviders.includes(id) &&
      !managedProviders.includes(draft.provider) &&
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
  function overrideModel(index, patch) {
    setRows((current) =>
      current.map((row, i) => {
        if (i !== index) return row
        const overrides = { ...row.model.overrides }
        if (patch) Object.assign(overrides, patch)
        else
          for (const key of ['reasoning', 'reasoningEfforts', 'reasoningToggle'])
            delete overrides[key]
        return { ...row, model: { ...row.model, overrides } }
      })
    )
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
  function importVertex(imported) {
    setVertex(imported.vertex)
    setTicket(imported.ticket)
    setKey('')
    setResult(null)
  }
  function updateSecret(value) {
    setKey(value)
    if (value) setTicket(null)
    setResult(null)
  }
  function selectAccount(next) {
    if (account && next?.id !== account.id) {
      setRows(modelRows({ models: [] }))
      setTestIndex(0)
    }
    setAccount(next)
    setResult(null)
  }
  function credentials() {
    if (isChatGPT) return { kind: 'keep' }
    if (isVertex && ticket) return { kind: 'vertex-import', ticket }
    if (clearKey) return { kind: 'remove' }
    if (key.trim()) return { kind: 'replace', secret: key.trim() }
    return { kind: 'keep' }
  }
  function definition() {
    return {
      ...draft,
      ...(isVertex ? { vertex } : {}),
      ...(isChatGPT ? { accountId: account?.id } : {}),
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

  function handleClearKeyChange(checked) {
    setClearKey(checked === true)
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
              options={
                connection && !managedProviders.includes(connection.provider)
                  ? providerOptions.filter((p) => !managedProviders.includes(p.value))
                  : providerOptions
              }
              disabled={
                Boolean(pending) ||
                Boolean(connection && managedProviders.includes(connection.provider))
              }
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
          {isVertex && (
            <VertexAccountFields
              vertex={vertex}
              onChange={setVertex}
              secret={key}
              onSecret={updateSecret}
              onImport={importVertex}
              onOpenGuide={openKeyPage}
              hasKey={Boolean(ticket || connection?.credentialConfigured)}
              onError={(message) => setResult({ ok: false, message })}
            />
          )}
          {isChatGPT && (
            <ChatGPTAccountFields
              boundId={connection?.accountId}
              account={account}
              onAccount={selectAccount}
            />
          )}
          {!isVertex && !isChatGPT && (
            <>
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
                  <div className="settings-inline-check">
                    <Checkbox
                      id={clearKeyId}
                      checked={clearKey}
                      onCheckedChange={handleClearKeyChange}
                    />
                    <label htmlFor={clearKeyId}>清除已保存的 Key</label>
                  </div>
                )}
              </div>
            </>
          )}
        </fieldset>
        <fieldset disabled={Boolean(pending)}>
          <legend>模型</legend>
          {rows.map(({ key, model }, index) => (
            <ModelRow
              key={key}
              index={index}
              model={model}
              isChatGPT={isChatGPT}
              isVertex={isVertex}
              account={account}
              canRemove={rows.length > 1}
              onEditModel={editModel}
              onOverrideModel={overrideModel}
              onRemoveModel={removeModel}
            />
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

function reasoningSummary(overrides) {
  const caps = overrides ?? {}
  const status = caps.reasoning?.status ?? 'auto'
  if (status === 'unsupported') return '不支持思考'
  if (status === 'supported') {
    const count = (caps.reasoningEfforts ?? []).length
    if (count > 0) return `思考 · ${count} 档`
    return caps.reasoningToggle ? '支持思考 · 可关' : '支持思考'
  }
  return '思考 · 自动'
}

function ModelRow({
  index,
  model,
  isChatGPT,
  isVertex,
  account,
  canRemove,
  onEditModel,
  onOverrideModel,
  onRemoveModel
}) {
  const [reasoningOpen, setReasoningOpen] = useState(false)
  const toggleId = useId()
  const panelId = useId()
  const caps = model.overrides ?? {}
  const status = caps.reasoning?.status ?? 'auto'
  const configured = status !== 'auto'

  function handleModelTextChange(event) {
    onEditModel(index, event.target.value)
  }

  function handleModelSelectChange(value) {
    onEditModel(index, value)
  }

  function handleTogglePanel() {
    setReasoningOpen((previous) => !previous)
  }

  function handleRemove() {
    onRemoveModel(index)
  }

  function handleSelectStatus(event) {
    const value = event.currentTarget.value
    if (value === 'auto') {
      onOverrideModel(index, null)
      return
    }
    onOverrideModel(index, {
      reasoning: { status: value, source: 'manual-override' },
      reasoningEfforts: [],
      reasoningToggle: false
    })
  }

  function handleToggleEffort(event) {
    const level = event.currentTarget.value
    const current = new Set(caps.reasoningEfforts ?? [])
    if (current.has(level)) current.delete(level)
    else current.add(level)
    // 按规范强度顺序输出，避免因点击顺序导致序列不稳定。
    const ordered = reasoningEffortOptions
      .map((option) => option.value)
      .filter((item) => current.has(item))
    onOverrideModel(index, { reasoningEfforts: ordered })
  }

  function handleToggleOffSwitch(checked) {
    onOverrideModel(index, { reasoningToggle: checked === true })
  }

  return (
    <div className="settings-model-item">
      <div className="settings-model-row">
        {isChatGPT ? (
          <SelectField
            label={'模型 ID ' + (index + 1)}
            value={model.id}
            placeholder={account?.connected ? '选择 GPT 模型' : '登录后可选模型'}
            disabled={!account?.connected}
            onChange={handleModelSelectChange}
            options={(account?.models ?? []).map((item) => ({
              value: item.id,
              label: item.label,
              keywords: [item.id]
            }))}
          />
        ) : (
          <Input
            required
            aria-label={'模型 ID ' + (index + 1)}
            placeholder={isVertex ? 'gemini-4-argon' : '模型 ID'}
            value={model.id}
            onChange={handleModelTextChange}
          />
        )}
        {!isChatGPT && (
          <Button
            type="button"
            size="sm"
            variant={reasoningOpen ? 'secondary' : 'outline'}
            className="model-reasoning-trigger"
            data-configured={configured || undefined}
            aria-expanded={reasoningOpen}
            aria-controls={panelId}
            onClick={handleTogglePanel}
          >
            <Brain size={14} aria-hidden="true" />
            <span>{reasoningSummary(model.overrides)}</span>
            <ChevronDown size={14} className="model-reasoning-chevron" aria-hidden="true" />
          </Button>
        )}
        {canRemove && (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label={'移除模型 ' + (index + 1)}
            onClick={handleRemove}
          >
            <X />
          </Button>
        )}
      </div>
      {!isChatGPT && reasoningOpen && (
        <div id={panelId} className="model-reasoning-panel">
          <div className="model-reasoning-section">
            <div className="model-reasoning-heading">
              <strong>模型思考能力</strong>
            </div>
            <div className="model-reasoning-modes" role="group" aria-label="思考能力声明">
              {reasoningStatuses.map((option) => (
                <Button
                  key={option.value}
                  type="button"
                  variant="ghost"
                  size="sm"
                  value={option.value}
                  aria-pressed={status === option.value}
                  onClick={handleSelectStatus}
                >
                  {option.label}
                </Button>
              ))}
            </div>
          </div>
          {status === 'supported' && (
            <>
              <div className="model-reasoning-section">
                <div className="model-reasoning-heading">
                  <strong>支持的思考强度</strong>
                  <small>留空则沿用模型思考默认强度。</small>
                </div>
                <div className="model-effort-pills" role="group" aria-label="支持的思考强度">
                  {reasoningEffortOptions.map((option) => {
                    const selected = Boolean(caps.reasoningEfforts?.includes(option.value))
                    return (
                      <button
                        key={option.value}
                        type="button"
                        role="checkbox"
                        value={option.value}
                        aria-checked={selected}
                        className="model-effort-pill"
                        onClick={handleToggleEffort}
                      >
                        <span className="model-effort-check" aria-hidden="true">
                          {selected && <Check size={11} strokeWidth={2.5} />}
                        </span>
                        <span>{option.label}</span>
                        <code>{option.value}</code>
                      </button>
                    )
                  })}
                </div>
              </div>
              <div className="model-reasoning-toggle">
                <div className="settings-inline-check">
                  <Checkbox
                    id={toggleId}
                    checked={Boolean(caps.reasoningToggle)}
                    onCheckedChange={handleToggleOffSwitch}
                  />
                  <label htmlFor={toggleId}>允许在对话中关闭思考</label>
                </div>
                <small>启用后，对话栏的思考强度菜单会提供「关闭」选项。</small>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** @param {object} props 连接快照与刷新入口。 @returns {import('react').ReactElement} 管理连接及同层编辑视图。 */
export function Connections({ client, connections, refresh }) {
  const [editor, setEditor] = useState(null)
  const [removing, setRemoving] = useState(null)
  const [pending, setPending] = useState(false)
  const [pendingOrder, setPendingOrder] = useState(null)
  const [error, setError] = useState(null)
  const stackRef = useRef(null)
  const displayConnections = orderItems(connections, pendingOrder)
  const sorting = useSortableRows({
    stackRef,
    ids: displayConnections.map((item) => item.id),
    disabled: pending,
    onReorder: commitOrder
  })
  const { dragState } = sorting

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
    } catch (failure) {
      setError(failure.message)
    } finally {
      setPending(false)
    }
  }
  async function commitOrder(nextIds) {
    const currentIds = connections.map((item) => item.id)
    if (pending || sameOrder(currentIds, nextIds)) return
    setPending(true)
    setPendingOrder(nextIds)
    setError(null)
    try {
      await client.settings.reorderConnections(nextIds)
      await refresh()
    } catch (failure) {
      setError(failure.message)
    } finally {
      setPending(false)
      setPendingOrder(null)
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
      {displayConnections.length === 0 && (
        <div className="settings-card settings-empty">
          <Cable size={22} />
          <div>
            <strong>还没有自有连接</strong>
            <p>添加连接后，可以为每段对话选择使用的模型。</p>
          </div>
        </div>
      )}
      <div
        ref={stackRef}
        className="settings-stack sortable-rows"
        data-dragging={dragState ? 'true' : undefined}
      >
        {dragState && (
          <div
            aria-hidden="true"
            className="sortable-drop-slot settings-drop-slot"
            style={{ top: dragState.slotTop, height: dragState.height }}
          />
        )}
        {displayConnections.map((item) => {
          const providerDetailName =
            providerPresets.find((preset) => preset.id === item.provider)?.name ?? item.provider

          return (
            <article
              className="settings-card"
              key={item.id}
              data-sort-id={item.id}
              data-dragging={dragState?.id === item.id ? 'true' : undefined}
              style={
                dragState
                  ? { transform: 'translateY(' + (dragState.offsets.get(item.id) ?? 0) + 'px)' }
                  : undefined
              }
            >
              <div className="settings-card-title">
                {displayConnections.length > 1 && (
                  <button
                    type="button"
                    className="settings-drag-handle"
                    aria-label={'拖动排序：' + item.name}
                    title="拖动排序，聚焦后可用上下方向键调整顺序"
                    aria-disabled={pending || undefined}
                    onPointerDown={(event) => sorting.startDrag(event, item.id)}
                    onPointerMove={sorting.moveDrag}
                    onPointerUp={sorting.dropDrag}
                    onPointerCancel={sorting.cancelDrag}
                    onKeyDown={(event) => sorting.handleSortKeyDown(event, item.id)}
                  >
                    <GripVertical size={16} aria-hidden="true" />
                  </button>
                )}
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
