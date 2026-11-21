import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, Cable, GripVertical, Plus, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Input } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { desktopHost } from '../../host/desktop.js'
import { providerPresets } from '../../../../shared/contracts/llm-providers.js'

import { ChatGPTAccountFields, VertexAccountFields } from './ManagedAccountFields.jsx'

const managedProviders = ['vertex', 'chatgpt']
const emptyVertex = { project: '', email: '', location: 'global' }
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
            </>
          )}
        </fieldset>
        <fieldset disabled={Boolean(pending)}>
          <legend>模型</legend>
          {rows.map(({ key, model }, index) => (
            <div className="settings-model-field" key={key}>
              {isChatGPT ? (
                <SelectField
                  label={'模型 ID ' + (index + 1)}
                  value={model.id}
                  placeholder={account?.connected ? '选择 GPT 模型' : '登录后可选模型'}
                  disabled={!account?.connected}
                  onChange={(value) => editModel(index, value)}
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
                  onChange={(event) => editModel(index, event.target.value)}
                />
              )}
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

function sameOrder(left, right) {
  return left.length === right.length && left.every((id, index) => id === right[index])
}

function orderConnections(connections, order) {
  if (!order) return connections
  const byId = new Map(connections.map((item) => [item.id, item]))
  const ordered = []
  for (const id of order) {
    const item = byId.get(id)
    if (!item) continue
    ordered.push(item)
    byId.delete(id)
  }
  for (const item of byId.values()) ordered.push(item)
  return ordered
}

function reorderByStep(ids, id, step) {
  const index = ids.indexOf(id)
  const nextIndex = index + step
  if (index < 0 || nextIndex < 0 || nextIndex >= ids.length) return null
  const next = [...ids]
  const [moved] = next.splice(index, 1)
  next.splice(nextIndex, 0, moved)
  return next
}

function reorderByPointer(stack, order, activeId, clientY) {
  if (!stack) return order
  const cards = [...stack.querySelectorAll('[data-connection-id]')]
  const currentIndex = order.indexOf(activeId)
  if (currentIndex < 0) return order
  let targetIndex = currentIndex
  for (const card of cards) {
    const cardId = card.dataset.connectionId
    if (!cardId || cardId === activeId) continue
    const cardIndex = order.indexOf(cardId)
    if (cardIndex < 0) continue
    const rect = card.getBoundingClientRect()
    const midpoint = rect.top + rect.height / 2
    if (cardIndex < currentIndex && clientY < midpoint) {
      targetIndex = Math.min(targetIndex, cardIndex)
    } else if (cardIndex > currentIndex && clientY > midpoint) {
      targetIndex = Math.max(targetIndex, cardIndex)
    }
  }
  if (targetIndex === currentIndex) return order
  const next = [...order]
  const [moved] = next.splice(currentIndex, 1)
  next.splice(targetIndex, 0, moved)
  return next
}

/** @param {object} props 连接快照与刷新入口。 @returns {import('react').ReactElement} 管理连接及同层编辑视图。 */
export function Connections({ client, connections, refresh }) {
  const [editor, setEditor] = useState(null)
  const [removing, setRemoving] = useState(null)
  const [pending, setPending] = useState(false)
  const [pendingOrder, setPendingOrder] = useState(null)
  const [dragState, setDragState] = useState(null)
  const [error, setError] = useState(null)
  const stackRef = useRef(null)
  const dragRef = useRef(null)
  const handleRefs = useRef(new Map())
  const refocusIdRef = useRef(null)
  const displayConnections = orderConnections(connections, dragState?.order ?? pendingOrder)

  useLayoutEffect(() => {
    const id = refocusIdRef.current
    if (!id) return
    refocusIdRef.current = null
    handleRefs.current.get(id)?.focus()
  }, [displayConnections])

  function bindHandleRef(id, element) {
    if (element) handleRefs.current.set(id, element)
    else handleRefs.current.delete(id)
  }

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
  function startDrag(event, connectionId) {
    if (event.button !== 0 || pending || dragRef.current) return
    const initialOrder = displayConnections.map((item) => item.id)
    if (initialOrder.length < 2) return
    event.currentTarget.setPointerCapture(event.pointerId)
    setRemoving(null)
    dragRef.current = {
      pointerId: event.pointerId,
      id: connectionId,
      initialOrder,
      currentOrder: initialOrder
    }
    setDragState({ id: connectionId, order: initialOrder })
  }
  function moveDrag(event) {
    const active = dragRef.current
    if (!active || active.pointerId !== event.pointerId) return
    const nextOrder = reorderByPointer(
      stackRef.current,
      active.currentOrder,
      active.id,
      event.clientY
    )
    if (sameOrder(active.currentOrder, nextOrder)) return
    active.currentOrder = nextOrder
    setDragState({ id: active.id, order: nextOrder })
  }
  function endDrag(event) {
    const active = dragRef.current
    if (!active || active.pointerId !== event.pointerId) return
    dragRef.current = null
    setDragState(null)
    if (!sameOrder(active.initialOrder, active.currentOrder)) {
      void commitOrder(active.currentOrder)
    }
  }
  function handleDragKeyDown(event, connectionId) {
    if (pending || dragState) return
    let step = 0
    if (event.key === 'ArrowUp') step = -1
    else if (event.key === 'ArrowDown') step = 1
    if (!step) return
    event.preventDefault()
    const ids = displayConnections.map((item) => item.id)
    const nextIds = reorderByStep(ids, connectionId, step)
    if (!nextIds) return
    // DOM 节点换位会打断聚焦，提交重排后在同帧布局阶段恢复手柄焦点。
    refocusIdRef.current = connectionId
    void commitOrder(nextIds)
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
      <div ref={stackRef} className="settings-stack" data-dragging={dragState ? 'true' : undefined}>
        {displayConnections.map((item) => {
          const providerDetailName =
            providerPresets.find((preset) => preset.id === item.provider)?.name ?? item.provider

          return (
            <article
              className="settings-card"
              key={item.id}
              data-connection-id={item.id}
              data-dragging={dragState?.id === item.id ? 'true' : undefined}
            >
              <div className="settings-card-title">
                {displayConnections.length > 1 && (
                  <button
                    type="button"
                    ref={(element) => bindHandleRef(item.id, element)}
                    className="settings-drag-handle"
                    aria-label={'拖动排序：' + item.name}
                    title="拖动排序，聚焦后可用上下方向键调整顺序"
                    aria-disabled={pending || undefined}
                    onPointerDown={(event) => startDrag(event, item.id)}
                    onPointerMove={moveDrag}
                    onPointerUp={endDrag}
                    onLostPointerCapture={endDrag}
                    onKeyDown={(event) => handleDragKeyDown(event, item.id)}
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
