import { useEffect, useRef, useState } from 'react'
import {
  ArrowUp,
  AudioLines,
  Check,
  Circle,
  Clock3,
  MessageCircle,
  Plus,
  Settings2,
  Shirt,
  Square,
  Trash2,
  UserRound,
  Users,
  Volume2,
  X
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { runtimeClient as client } from '@/runtime/client'
import { createPerformanceRuntime } from '@/performance/runtime'
import { Drafts } from '@/features/chat/drafts'
import { displayMessage } from '@/features/chat/message-display'
import { Modal, NewChatForm, PersonaForm, SettingsForm } from './dialogs'
import { CharacterModelsForm } from './character-models'

const runtime = createPerformanceRuntime(window.dsakiko, client)
const drafts = new Drafts()
const activityLabels = { idle: '可以开始新的对话', generating: '正在思考', presenting: '正在演出' }

function Avatar({ asset, name, small = false }) {
  const [resolved, setResolved] = useState({ id: null, url: '' })
  const assetId = asset?.id
  const url = resolved.id === assetId ? resolved.url : ''
  useEffect(() => {
    let active = true
    if (assetId)
      client
        .resolveAsset({ id: assetId })
        .then((result) => {
          if (active) setResolved({ id: assetId, url: result.url })
        })
        .catch(() => {})
    return () => {
      active = false
    }
  }, [assetId])
  return (
    <span className={`avatar ${small ? 'avatar-small' : ''}`}>
      {url ? <img src={url} alt="" /> : name?.slice(0, 1) || <UserRound size={18} />}
    </span>
  )
}

function Stage({ active, characters, onModels }) {
  const host = useRef(null)
  const [state, setState] = useState(runtime.surface.snapshot())
  useEffect(() => {
    runtime.surface.mount(host.current)
    return runtime.surface.observe(setState)
  }, [])
  const guide = state.current
  const speaker = state.scene?.slots.find((slot) => slot.id === guide?.slotId)
  function renderSlot(slot) {
    const character = characters.find((item) => item.id === slot.id)
    const model = character?.models.find((item) => item.id === character.selectedModelId)
    return (
      <div className="stage-name" key={slot.id} style={{ left: `${slot.layout.x * 100}%` }}>
        <i className={guide?.slotId === slot.id ? 'speaking' : ''} />
        <span>
          {slot.displayName}
          {model && <small>{model.label}</small>}
        </span>
      </div>
    )
  }
  return (
    <section className="stage-pane" aria-label="角色演出" aria-busy={state.loading}>
      <div className="stage-topline">
        <span>
          <span className="stage-light" />
          {active ? '对话舞台' : '等待登场'}
        </span>
        <button
          type="button"
          className="stage-wardrobe"
          onClick={onModels}
          aria-label="切换舞台装扮"
        >
          <Shirt size={13} />
          切换装扮
        </button>
      </div>
      <div className="stage-floor" />
      <div className="live2d-host" ref={host} />
      {state.loading && (
        <div className="stage-loading" role="status">
          <span className="stage-light pulse" />
          正在布置舞台…
        </div>
      )}
      {!active && (
        <div className="stage-empty">
          <span className="stage-emblem">祥</span>
          <h2>让故事，从这里开始。</h2>
          <p>选择角色，开启一段新的对话。</p>
        </div>
      )}
      {active && <div className="stage-cast">{state.scene?.slots.map(renderSlot)}</div>}
      {guide && (
        <div className="stage-subtitle">
          <span>{speaker?.displayName}</span>
          <p>{guide.translation || guide.text}</p>
        </div>
      )}
      {state.problems.length > 0 && <div className="stage-problem">{state.problems.join(' ')}</div>}
      <div className="stage-footnote">
        <AudioLines size={14} />
        <span>{guide ? '正在播放当前对话' : '后台对话会继续推进'}</span>
      </div>
    </section>
  )
}

function Message({ entry, display, character, persona, busy, chatId, onError }) {
  const content = displayMessage(entry, display)
  async function replay() {
    try {
      await client.replayMessage(chatId, entry.id)
    } catch (error) {
      onError(error.message)
    }
  }
  if (!content.visible) return null
  if (entry.kind === 'tool')
    return (
      <div className="tool-record">
        <Clock3 size={14} />
        <span>当前时间</span>
        <small>{entry.toolCall?.result?.local || entry.toolCall?.status}</small>
        {entry.toolCall?.status === 'succeeded' && <Check size={13} />}
      </div>
    )
  if (entry.kind === 'reasoning')
    return (
      <details className="reasoning">
        <summary>思考过程</summary>
        <p>{entry.text}</p>
      </details>
    )
  const isUser = entry.kind === 'user'
  const name = isUser ? persona?.displayName || '你' : character?.displayName || entry.speakerId
  return (
    <article className={`message ${isUser ? 'message-user' : ''}`}>
      <Avatar asset={isUser ? persona?.avatar : character?.avatar} name={name} small />
      <div className="message-content">
        <div className="message-heading">
          <strong>{name}</strong>
          {!isUser && (
            <button
              type="button"
              onClick={replay}
              disabled={busy}
              aria-label={`回放${name}的这句台词`}
            >
              <Volume2 size={13} />
            </button>
          )}
        </div>
        <div className="message-bubble">
          <p>{content.text}</p>
          {content.translation && <p className="message-translation">{content.translation}</p>}
        </div>
      </div>
    </article>
  )
}

/**
 * 桌面聊天工作区；业务状态来自 Node，草稿与演出分别独立存活。
 * @returns {import('react').ReactElement} 原型主界面。
 */
export function App() {
  const [characters, setCharacters] = useState([])
  const [personas, setPersonas] = useState([])
  const [settings, setSettings] = useState(null)
  const [snapshots, setSnapshots] = useState({})
  const [selectedId, setSelectedId] = useState(null)
  const selectedRef = useRef(null)
  const [draft, setDraft] = useState('')
  const [modal, setModal] = useState(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('starting')
  const [sending, setSending] = useState(false)
  const scroll = useRef(null)
  const follow = useRef(true)
  const active = snapshots[selectedId]
  const busy = active?.activity !== 'idle'
  const chats = Object.values(snapshots)
  const backgroundCount = chats.filter(
    (item) => item.chat.id !== selectedId && item.activity !== 'idle'
  ).length

  async function refresh() {
    const data = await client.getBootstrap()
    setCharacters(data.characters)
    setPersonas(data.personas)
    setSettings(data.settings)
    setSnapshots((previous) => {
      const next = { ...previous }
      for (const snapshot of data.conversations)
        if (!next[snapshot.chat.id] || snapshot.revision >= next[snapshot.chat.id].revision)
          next[snapshot.chat.id] = snapshot
      return next
    })
    if (data.problem) setError(data.problem.message)
  }
  useEffect(() => {
    let active = true
    const unsubscribe = client.onUpdate((update) => {
      if (!active) return
      if (update.type === 'conversation')
        setSnapshots((previous) => {
          const snapshot = update.snapshot
          if ((previous[snapshot.chat.id]?.revision ?? -1) >= snapshot.revision) return previous
          return { ...previous, [snapshot.chat.id]: snapshot }
        })
      if (update.type === 'deleted')
        setSnapshots((previous) => {
          const next = { ...previous }
          delete next[update.chatId]
          return next
        })
      if (update.type === 'catalog') void refresh().catch((error) => setError(error.message))
    })
    client
      .performanceReady()
      .then(refresh)
      .then(() => {
        if (active) setStatus('ready')
      })
      .catch((error) => {
        if (active) {
          setError(error.message)
          setStatus('error')
        }
      })
    return () => {
      active = false
      unsubscribe()
    }
  }, [])
  useEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [active?.revision])

  async function selectChat(id) {
    try {
      await client.selectConversation(id)
      selectedRef.current = id
      setSelectedId(id)
      setDraft(drafts.get(id))
      setError('')
      follow.current = true
    } catch (error) {
      setError(error.message)
    }
  }
  function clickChat(event) {
    void selectChat(event.currentTarget.dataset.id)
  }
  function changeDraft(event) {
    const value = event.target.value
    drafts.update(selectedId, value)
    setDraft(value)
  }
  async function send(event) {
    event.preventDefault()
    if (!active || busy || sending || !draft.trim()) return
    const chatId = selectedId,
      prepared = drafts.prepare(chatId)
    setSending(true)
    setError('')
    try {
      await client.createTurn(chatId, prepared.submission, { requestId: prepared.requestId })
      drafts.acknowledge(chatId, prepared.requestId)
      if (selectedRef.current === chatId) setDraft(drafts.get(chatId))
      follow.current = true
    } catch (error) {
      setError(error.message)
    } finally {
      setSending(false)
    }
  }
  function keyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send(event)
    }
  }
  async function stop() {
    try {
      await client.stopTurn(selectedId, active.activeTurnId)
    } catch (error) {
      setError(error.message)
    }
  }
  async function createChat(options) {
    const id = await client.createChat(options)
    await refresh()
    await selectChat(id)
  }
  function newChat() {
    setModal('new')
  }
  function editPersonas() {
    setModal('personas')
  }
  function editModels() {
    setModal('models')
  }
  async function openSettings() {
    try {
      await client.selectConversation(null)
      setModal('settings')
    } catch (error) {
      setError(error.message)
    }
  }
  function closeModal() {
    const wasSettings = modal === 'settings'
    setModal(null)
    if (wasSettings && selectedId) void selectChat(selectedId)
  }
  function requestDelete() {
    setModal('delete')
  }
  async function deleteChat() {
    try {
      await client.deleteChat(selectedId)
      selectedRef.current = null
      setSelectedId(null)
      setDraft('')
      setModal(null)
    } catch (error) {
      setError(error.message)
    }
  }
  function onScroll() {
    const element = scroll.current
    follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
  }
  function dismissError() {
    setError('')
  }
  function renderChat(snapshot) {
    const chat = snapshot.chat
    const character = characters.find((item) => item.id === chat.characterIds[0])
    const isBusy = snapshot.activity !== 'idle'
    return (
      <button
        key={chat.id}
        className={`chat-entry ${chat.id === selectedId ? 'selected' : ''}`}
        data-id={chat.id}
        onClick={clickChat}
      >
        <Avatar asset={character?.avatar} name={chat.title} />
        <span className="chat-entry-copy">
          <strong>{chat.title}</strong>
          <small>
            {isBusy
              ? activityLabels[snapshot.activity]
              : chat.turns.at(-1)?.messages.at(-1)?.text || '一段新的故事'}
          </small>
        </span>
        {isBusy && <span className="activity-dot" />}
      </button>
    )
  }
  function renderTurn(turn) {
    return (
      <section className="turn" key={turn.id}>
        {turn.messages.map(renderMessage)}
        {turn.metadata.state === 'interrupted' && <p className="interrupted-note">本轮已停止</p>}
      </section>
    )
  }
  function renderMessage(entry) {
    return (
      <Message
        key={entry.id}
        entry={entry}
        display={active.display.find((item) => item.messageId === entry.id)}
        character={characters.find((item) => item.id === entry.speakerId)}
        persona={active.chat.userPersona}
        busy={busy}
        chatId={selectedId}
        onError={setError}
      />
    )
  }
  const currentError = error || active?.problem?.message
  return (
    <div className="desktop-shell">
      <aside className="sidebar">
        <header className="brand">
          <span className="brand-symbol">祥</span>
          <div>
            <h1>数字小祥</h1>
            <span>D_SAKIKO</span>
          </div>
        </header>
        <Button className="new-chat-button" onClick={newChat}>
          <Plus size={17} />
          新的对话
        </Button>
        <div className="sidebar-label">
          <span>我的对话</span>
          <span>{chats.length}</span>
        </div>
        <nav className="chat-list" aria-label="聊天列表">
          {chats.map(renderChat)}
          {!chats.length && (
            <p className="sidebar-empty">
              还没有对话。
              <br />
              让喜欢的角色登场吧。
            </p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <button onClick={editModels}>
            <Shirt size={17} />
            角色装扮<span>Live2D</span>
          </button>
          <button onClick={editPersonas}>
            <UserRound size={17} />
            我的人格<span>{personas.length}</span>
          </button>
          <button onClick={openSettings}>
            <Settings2 size={17} />
            设置
          </button>
          <div role="status" data-status={status} className="connection-state">
            <i />
            {status === 'ready' ? '本机已就绪' : '正在连接…'}
            {backgroundCount > 0 && <span>{backgroundCount} 个后台对话</span>}
          </div>
        </div>
      </aside>
      <main className="main-workspace">
        <header className="workspace-header">
          <div>
            <span className="workspace-eyebrow">
              {active?.chat.mode === 'scripted-dialogue' ? (
                <Users size={14} />
              ) : (
                <MessageCircle size={14} />
              )}
              {active ? '正在对话' : '对话工作区'}
            </span>
            <h2>{active?.chat.title || '与你相遇的每一刻'}</h2>
          </div>
          <div className="header-actions">
            <span className="persona-badge">
              <UserRound size={13} />
              {active?.chat.userPersona?.displayName || '默认身份'}
            </span>
            {active && (
              <Button
                variant="ghost"
                size="icon"
                onClick={requestDelete}
                disabled={busy}
                aria-label="删除当前对话"
              >
                <Trash2 size={16} />
              </Button>
            )}
          </div>
        </header>
        <div className="workspace-body">
          <Stage active={active} characters={characters} onModels={editModels} />
          <section className="chat-pane" aria-label="消息与输入">
            <div className="conversation-topline">
              <span>对话记录</span>
              <span>{active ? activityLabels[active.activity] : '等待开始'}</span>
            </div>
            <div className="messages" ref={scroll} onScroll={onScroll}>
              {active?.chat.turns.map(renderTurn)}
              {!active?.chat.turns.length && (
                <div className="message-empty">
                  <MessageCircle size={27} strokeWidth={1.4} />
                  <h3>{active ? `向${active.chat.title}打个招呼` : '今天，想和谁聊聊？'}</h3>
                  <p>
                    {active
                      ? '说说今天发生的事，或一起聊聊此刻。'
                      : '新建对话后，消息会保存在这里。'}
                  </p>
                  {!active && (
                    <Button variant="outline" onClick={newChat}>
                      <Plus size={15} />
                      选择角色
                    </Button>
                  )}
                </div>
              )}
            </div>
            {currentError && (
              <div className="error-banner" role="alert">
                <span>{currentError}</span>
                {error && (
                  <button onClick={dismissError} aria-label="关闭提示">
                    <X size={14} />
                  </button>
                )}
              </div>
            )}
            <form className="composer" onSubmit={send}>
              <textarea
                aria-label="消息"
                placeholder={active ? '说些什么吧…' : '先选择一个对话'}
                value={draft}
                onChange={changeDraft}
                onKeyDown={keyDown}
                disabled={!active}
                rows={3}
              />
              <div className="composer-footer">
                <span>
                  {busy && active ? (
                    <>
                      <Circle size={9} className="pulse" />
                      本轮结束后可以继续发送
                    </>
                  ) : (
                    'Enter 发送 · Shift + Enter 换行'
                  )}
                </span>
                {busy && active ? (
                  <Button type="button" variant="secondary" onClick={stop}>
                    <Square size={13} />
                    停止
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    disabled={!active || !draft.trim() || sending}
                    aria-label="发送消息"
                  >
                    <ArrowUp size={18} />
                  </Button>
                )}
              </div>
            </form>
          </section>
        </div>
      </main>
      {modal === 'new' && (
        <Modal title="开启新的对话" onClose={closeModal}>
          <NewChatForm
            characters={characters}
            personas={personas}
            onCreate={createChat}
            onClose={closeModal}
            Avatar={Avatar}
          />
        </Modal>
      )}
      {modal === 'personas' && (
        <Modal title="我的人格" onClose={closeModal}>
          <PersonaForm
            personas={personas}
            characters={characters}
            client={client}
            onSaved={refresh}
          />
        </Modal>
      )}
      {modal === 'settings' && settings && (
        <Modal title="设置" onClose={closeModal} wide>
          <SettingsForm initial={settings} client={client} onSaved={refresh} />
        </Modal>
      )}
      {modal === 'models' && (
        <Modal title="角色装扮" onClose={closeModal} wide>
          <CharacterModelsForm
            characters={characters}
            initialId={active?.chat.characterIds[0]}
            snapshots={chats}
            onSaved={setCharacters}
            onClose={closeModal}
          />
        </Modal>
      )}
      {modal === 'delete' && (
        <Modal title="删除对话" onClose={closeModal}>
          <div className="form-stack">
            <p>删除「{active?.chat.title}」及其聊天记录？此操作无法撤销。</p>
            <footer className="form-footer">
              <Button variant="outline" onClick={closeModal}>
                保留对话
              </Button>
              <Button variant="destructive" onClick={deleteChat}>
                删除对话
              </Button>
            </footer>
          </div>
        </Modal>
      )}
    </div>
  )
}
