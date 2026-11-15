import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import {
  ArrowUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  GripHorizontal,
  Maximize,
  MessageCircle,
  Plus,
  RotateCcw,
  Settings,
  Shirt,
  Square,
  Trash2,
  ZoomIn,
  ZoomOut
} from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import logo from '../../assets/welcome/logo.svg'
import wordmark from '../../assets/welcome/wordmark.svg'
import { previewCharacters } from '../onboarding/characters.js'
import { desktopHost } from '../../host/desktop.js'
import { HomeFlow, hasConnection, selectedModel } from './home-flow.js'
import { StageController } from './stage-controller.js'
import { HistoryPanel } from './HistoryPanel.jsx'
import { NoticeStack } from './NoticeStack.jsx'
import { DeleteDialog } from './DeleteDialog.jsx'
import { VoiceInput, VoiceInstallDialog } from './VoiceInput.jsx'
import { SettingsPanel } from '../settings/SettingsPanel.jsx'
import { ResourceDownloadPage } from '../settings/Resources.jsx'
import './home.css'

function Avatar({ role, client }) {
  const [url, setUrl] = useState(null)
  useEffect(() => {
    let current = true
    let lease
    if (role.avatar)
      void client.assets
        .access(role.avatar)
        .then((access) => {
          lease = access.leaseId
          if (current) setUrl(access.url)
          else void client.assets.releaseAccess(lease).catch(() => {})
        })
        .catch(() => {})
    return () => {
      current = false
      if (lease) void client.assets.releaseAccess(lease).catch(() => {})
    }
  }, [client, role.avatar])
  const fallback = previewCharacters.find((item) => item.id === role.id)?.avatar
  if (url || fallback) return <img src={url ?? fallback} alt="" width="40" height="40" />
  return (
    <span className="avatar-placeholder">
      {(role.displayName ?? role.name ?? '角').slice(0, 1)}
    </span>
  )
}
function ChatItem({ chat, selected, flow, onDelete }) {
  function select() {
    flow.selectChat(chat)
  }
  function remove() {
    onDelete(chat)
  }
  return (
    <div className={'home-chat-row' + (selected ? ' selected' : '')}>
      <button onClick={select} aria-current={selected ? 'page' : undefined}>
        {chat.lastReply || '暂无回复'}
      </button>
      <Button
        className="home-chat-delete"
        size="icon-sm"
        variant="ghost"
        onClick={remove}
        aria-label={'删除对话：' + chat.title}
      >
        <Trash2 size={14} />
      </Button>
    </div>
  )
}
function CharacterGroup({ role, state, flow, client, expanded, onToggle, onDelete }) {
  const active = state.target.characterId === role.id
  const listId = useId()
  const chats = state.chats.filter(
    (chat) =>
      chat.mode === 'single-character' && !chat.participantIssue && chat.characterIds[0] === role.id
  )
  function toggle(event) {
    onToggle(role.id, event.currentTarget)
  }
  function create() {
    flow.newChat(role.id)
  }
  return (
    <section className="home-role-group">
      <button
        className={'home-role' + (active ? ' selected' : '')}
        aria-current={active ? 'true' : undefined}
        aria-expanded={expanded}
        aria-controls={listId}
        onClick={toggle}
      >
        <Avatar role={role} client={client} />
        <span className="home-role-name">{role.displayName ?? role.name}</span>
        <ChevronRight className="home-role-chevron" size={16} aria-hidden="true" />
      </button>
      {/* 收起时保留列表布局供过渡使用，并立即移除内部控件的焦点与交互。 */}
      <div
        className="home-role-disclosure"
        data-expanded={expanded}
        id={listId}
        inert={!expanded}
        aria-hidden={!expanded}
      >
        <div className="home-role-disclosure-clip">
          <div className="home-role-chats">
            <Button variant="ghost" className="home-new-chat" onClick={create}>
              <Plus size={14} />
              新对话
            </Button>
            {chats.map((chat) => (
              <ChatItem
                key={chat.id}
                chat={chat}
                selected={state.target.chatId === chat.id}
                flow={flow}
                onDelete={onDelete}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

/** @typedef {{button: HTMLButtonElement, list: HTMLElement, nav: HTMLElement, offset: number}} RoleScrollAnchor 切换到下方分组时，记录角色卡在滚动区域内的位置。 */
/** @param {RoleScrollAnchor} anchor 本次折叠的滚动锚点。 @returns {() => void} 中止滚动补偿并清理监听。 */
function keepRoleInView({ button, list, nav, offset }) {
  const controller = new AbortController()
  const firstAction = document
    .getElementById(button.getAttribute('aria-controls'))
    ?.querySelector('.home-new-chat')
  // 浏览器可能选中正在收起的旧内容作为锚点，这次过渡由新角色卡来决定滚动位置。
  const previousAnchor = nav.style.overflowAnchor
  nav.style.overflowAnchor = 'none'

  function adjustScroll() {
    const header = button.getBoundingClientRect()
    const roomForAction = firstAction
      ? firstAction.getBoundingClientRect().bottom - header.top
      : header.height
    const target = Math.max(0, Math.min(offset, nav.clientHeight - roomForAction))
    const position = header.top - nav.getBoundingClientRect().top
    nav.scrollTop += position - target
  }
  const observer = new ResizeObserver(adjustScroll)
  function stop() {
    if (controller.signal.aborted) return
    controller.abort()
    observer.disconnect()
    nav.style.overflowAnchor = previousAnchor
  }

  adjustScroll()
  const transition = list
    .getAnimations()
    .find((animation) => animation.transitionProperty === 'grid-template-rows')
  if (transition) {
    observer.observe(list)
    void transition.finished.then(() => {
      if (!controller.signal.aborted) adjustScroll()
      stop()
    }, stop)
    for (const event of ['wheel', 'touchstart', 'pointerdown', 'keydown'])
      nav.addEventListener(event, stop, { passive: true, signal: controller.signal })
  } else stop()
  return stop
}

function CharacterGroups({ state, flow, client, onDelete }) {
  // 初次展开恢复的角色，之后折叠状态只由侧栏操作决定。
  const [expandedRoleId, setExpandedRoleId] = useState(state.target.characterId)
  const scrollAnchor = useRef(null)
  useLayoutEffect(() => {
    const anchor = scrollAnchor.current
    scrollAnchor.current = null
    if (anchor) return keepRoleInView(anchor)
  }, [expandedRoleId])

  function toggleRole(roleId, button) {
    const nav = button.closest('.home-navigation')
    const list = nav?.querySelector('.home-role-disclosure[data-expanded="true"]')
    const closingAbove = list?.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING
    scrollAnchor.current = closingAbove
      ? {
          button,
          list,
          nav,
          offset: button.getBoundingClientRect().top - nav.getBoundingClientRect().top
        }
      : null
    setExpandedRoleId((current) => (current === roleId ? null : roleId))
  }

  return state.characters.map((role) => (
    <CharacterGroup
      key={role.id}
      role={role}
      state={state}
      flow={flow}
      client={client}
      expanded={expandedRoleId === role.id}
      onToggle={toggleRole}
      onDelete={onDelete}
    />
  ))
}
function Toolbar({ onZoom, onReset }) {
  const [position, setPosition] = useState(null)
  const drag = useRef(null)
  function start(event) {
    const rect = event.currentTarget.parentElement.getBoundingClientRect()
    const parent = event.currentTarget.parentElement.parentElement.getBoundingClientRect()
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      left: rect.left - parent.left,
      top: rect.top - parent.top,
      width: parent.width,
      height: parent.height,
      toolbarHeight: rect.height
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function move(event) {
    const origin = drag.current
    if (!origin) return
    setPosition({
      left: Math.max(8, Math.min(origin.width - 56, origin.left + event.clientX - origin.x)),
      top: Math.max(
        8,
        Math.min(origin.height - origin.toolbarHeight - 8, origin.top + event.clientY - origin.y)
      )
    })
  }
  function end() {
    drag.current = null
  }
  function increase() {
    onZoom(1.1)
  }
  function decrease() {
    onZoom(1 / 1.1)
  }
  function reset() {
    onReset()
  }
  return (
    <div
      className="stage-toolbar"
      style={position ? { ...position, bottom: 'auto' } : undefined}
      aria-label="舞台工具"
    >
      <button
        className="toolbar-grip"
        aria-label="拖动舞台工具栏"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={end}
        onLostPointerCapture={end}
      >
        <GripHorizontal size={16} />
      </button>
      <Button variant="ghost" size="icon" aria-label="放大角色" onClick={increase}>
        <ZoomIn />
      </Button>
      <Button variant="ghost" size="icon" aria-label="缩小角色" onClick={decrease}>
        <ZoomOut />
      </Button>
      <Button variant="ghost" size="icon" aria-label="重置角色视图" onClick={reset}>
        <RotateCcw />
      </Button>
      <hr />
      <Button variant="ghost" size="icon" aria-label="切换服装" disabled>
        <Shirt />
      </Button>
      <Button variant="ghost" size="icon" aria-label="全窗口舞台" disabled>
        <Maximize />
      </Button>
    </div>
  )
}
function Subtitle({ state }) {
  if (!state.text) return <div className="home-subtitle" />
  const characters = Array.from(state.text)
  return (
    <section className="home-subtitle" aria-label="当前台词">
      <div className="subtitle-name">{state.name}</div>
      <div className="subtitle-body">
        <div className="subtitle-text" aria-label={state.text}>
          {characters.map((character, index) => (
            <span
              key={index}
              aria-hidden="true"
              className={index < state.revealed ? 'spoken' : undefined}
            >
              {character}
            </span>
          ))}
        </div>
        {state.translation && <div className="subtitle-translation">{state.translation}</div>}
      </div>
    </section>
  )
}
function ModelPicker({ state, flow }) {
  if (!state.connections.some((item) => item.models.length)) return null
  const selection = selectedModel(state)
  function change(event) {
    if (event.target.value) void flow.chooseModel(JSON.parse(event.target.value))
  }
  return (
    <label className="home-model-picker">
      <select
        aria-label="对话模型"
        className="field-input"
        value={hasConnection(state) && selection ? JSON.stringify(selection) : ''}
        onChange={change}
      >
        <option value="" disabled>
          选择模型
        </option>
        {state.connections
          .filter((item) => item.models.length)
          .map((item) => (
            <optgroup key={item.id} label={item.name}>
              {item.models.map((model) => {
                const value = JSON.stringify({ connectionId: item.id, modelId: model.id })
                return (
                  <option value={value} key={model.id}>
                    {model.id}
                  </option>
                )
              })}
            </optgroup>
          ))}
      </select>
      <ChevronDown className="home-model-picker-chevron" size={14} aria-hidden="true" />
    </label>
  )
}

const replyPhaseLabels = {
  generation: '正在生成回复…',
  speech: '正在合成语音…',
  presentation: '正在播放回复…'
}

function Composer({ state, flow, asrReady, onVoiceInstall, onConfigure, onSend, onStopReplay }) {
  const configured = hasConnection(state)
  const readonly =
    state.target.tab === 'scripted-dialogue' ||
    Boolean(state.snapshot?.participantIssue) ||
    !state.target.characterId
  const active = Boolean(state.snapshot?.activeTurnId)
  const canSend =
    configured &&
    !readonly &&
    !state.sending &&
    !active &&
    Boolean(state.draft.trim()) &&
    (!state.target.chatId || state.snapshot?.admission.canGenerate)
  const textarea = useRef(null)
  useEffect(() => {
    if (!textarea.current) return
    textarea.current.style.height = 'auto'
    textarea.current.style.height =
      Math.min(144, Math.max(28, textarea.current.scrollHeight)) + 'px'
  }, [state.draft])
  function change(event) {
    flow.setDraft(event.target.value)
  }
  function keydown(event) {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.nativeEvent.isComposing ||
      event.keyCode === 229
    )
      return
    event.preventDefault()
    if (canSend) void onSend()
  }
  let placeholder = '和角色说点什么…'
  if (readonly) placeholder = '此对话仅供阅读'
  const snapshot = state.snapshot
  const saveFailed = snapshot?.persistence.state === 'failed'
  const latestTurnId = snapshot?.chat.turns.at(-1)?.id
  const execution = snapshot?.executions.find((item) => item.turnId === latestTurnId)
  const executionProblem = execution?.problem
  const turnProblem =
    !state.sending && executionProblem?.code !== 'cancelled' ? executionProblem?.message : null
  const problem =
    state.problem ??
    snapshot?.persistence.problem?.message ??
    snapshot?.problem?.message ??
    turnProblem
  const replyStatus = execution?.state === 'running' ? replyPhaseLabels[execution.phase] : null
  const status = state.sending ? '正在发送…' : replyStatus
  const hasAvailableModels = state.connections.some((item) => item.models.length > 0)
  let modelControl = null
  if (!readonly) {
    if (hasAvailableModels) modelControl = <ModelPicker state={state} flow={flow} />
    else if (!configured)
      modelControl = (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="home-model-configure"
          onClick={onConfigure}
        >
          前往配置模型
        </Button>
      )
  }
  const submitButton = active ? (
    <Button size="icon-sm" className="home-composer-send" aria-label="停止生成" onClick={flow.stop}>
      <Square size={16} />
    </Button>
  ) : (
    <Button
      size="icon-sm"
      className="home-composer-send"
      aria-label="发送消息"
      disabled={!canSend}
      onClick={onSend}
    >
      <ArrowUp />
    </Button>
  )
  return (
    <div className="home-composer-group">
      {problem && (
        <div className="composer-problem" role="alert">
          {problem}
          {saveFailed && (
            <Button size="sm" variant="outline" onClick={flow.retrySave}>
              重试保存
            </Button>
          )}
        </div>
      )}
      <div className="home-composer">
        <textarea
          ref={textarea}
          aria-label="消息"
          placeholder={placeholder}
          value={state.draft}
          onChange={change}
          onKeyDown={keydown}
          disabled={readonly}
          rows={1}
        />
        <div className="home-composer-toolbar">
          <div className="home-composer-leading">
            <Button variant="ghost" size="icon-sm" disabled aria-label="添加附件">
              <Plus />
            </Button>
            {onStopReplay && (
              <Button size="sm" variant="ghost" className="home-stop-replay" onClick={onStopReplay}>
                <Square /> 停止回放
              </Button>
            )}
            {status && (
              <span className="composer-status" role="status">
                {status}
              </span>
            )}
          </div>
          <div className="home-composer-actions">
            {modelControl}
            <VoiceInput
              client={flow.client}
              flow={flow}
              disabled={readonly}
              ready={asrReady}
              onInstall={onVoiceInstall}
            />
            {submitButton}
          </div>
        </div>
      </div>
    </div>
  )
}

/** @param {{client: import('../../backend/client.js').BackendClient, setup: object, setupState: object}} props 应用共享连接和后台任务。 @returns {import('react').ReactElement} 正式主页，历史悬浮于稳定舞台之上。 */
export function HomePage({ client, setup, setupState }) {
  const [flow] = useState(() => new HomeFlow(client))
  const state = useSyncExternalStore(flow.subscribe, flow.getSnapshot)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [turnReplay, setTurnReplay] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [settings, setSettings] = useState(null)
  const [resource, setResource] = useState(null)
  const [voiceInstall, setVoiceInstall] = useState(false)
  const [stageState, setStageState] = useState({
    text: '',
    name: '',
    revealed: 0,
    problem: null,
    loading: false
  })
  const canvas = useRef(null)
  const stage = useRef(null)
  const hostVisibility = useRef(true)
  const downloadPageOpen = useRef(false)
  const sequenceId = state.snapshot?.presentation.sequenceId ?? null
  const currentReplay = turnReplay?.chatId === state.target.chatId ? turnReplay : null
  useEffect(() => {
    void flow.mount()
    return () => flow.dispose()
  }, [flow])
  useEffect(() => {
    if (deleting || resource || voiceInstall) return
    const platform = navigator.userAgentData?.platform ?? navigator.platform
    const isMac = platform.toLowerCase().startsWith('mac')
    function handleSettingsShortcut(event) {
      const hasModifier = isMac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey
      if (
        !hasModifier ||
        event.key !== ',' ||
        event.altKey ||
        event.shiftKey ||
        event.defaultPrevented ||
        event.isComposing
      )
        return
      event.preventDefault()
      if (!event.repeat) setSettings((current) => current ?? { section: 'connections' })
    }
    window.addEventListener('keydown', handleSettingsShortcut)
    return () => window.removeEventListener('keydown', handleSettingsShortcut)
  }, [deleting, resource, voiceInstall])
  useEffect(() => {
    const controller = new StageController(client, setStageState)
    stage.current = controller
    controller.mount(canvas.current)
    const host = desktopHost()
    let active = true
    let visible = true
    function sync() {
      hostVisibility.current = visible
      if (active) controller.setVisible(visible && !document.hidden && !downloadPageOpen.current)
    }
    const stop = host.onWindowVisibility((value) => {
      visible = value
      sync()
    })
    void host
      .getWindowVisibility()
      .then((value) => {
        visible = value
        sync()
      })
      .catch(() => {})
    document.addEventListener('visibilitychange', sync)
    return () => {
      active = false
      stop()
      document.removeEventListener('visibilitychange', sync)
      controller.dispose()
      stage.current = null
    }
  }, [client])
  useEffect(() => {
    void stage.current?.select(state.target.characterId, sequenceId)
  }, [state.target.characterId, state.target.chatId, sequenceId])
  useEffect(() => {
    stage.current?.setHistory(state.snapshot)
  }, [state.snapshot, state.target.chatId])
  function zoomModel(factor) {
    stage.current?.zoom(factor)
  }
  function resetModel() {
    stage.current?.resetLayout()
  }
  function showHistory() {
    setHistoryOpen(true)
  }
  function hideHistory() {
    setHistoryOpen(false)
  }
  function playHistoryAudio(audio, signal, onStop) {
    const controller = stage.current
    if (!controller) return onStop()
    controller.stopReplay()
    controller.playHistoryAudio(audio, signal, onStop)
  }
  function stopHistoryTurn() {
    stage.current?.stopReplay()
  }
  function playHistoryTurn(turnId) {
    const chatId = state.target.chatId
    const controller = stage.current
    if (!chatId || !controller) return
    if (currentReplay?.id === turnId && !currentReplay.error) {
      controller.stopReplay()
      return
    }
    const request = { chatId, id: turnId, error: null }
    setTurnReplay(request)
    void controller.replayTurn(chatId, turnId, (error) => {
      setTurnReplay((current) => {
        if (current !== request) return current
        return error ? { ...request, error: error.message || '本轮回复无法回放。' } : null
      })
    })
  }
  function sendMessage() {
    return flow.send((chatId) => stage.current?.stopReplay(chatId))
  }
  function dialogueTab() {
    flow.setTab('single-character')
  }
  function theatreTab() {
    flow.setTab('scripted-dialogue')
  }
  function cancelDelete() {
    setDeleting(null)
  }
  function openSettings(section = 'connections', target) {
    setSettings({ section, target })
  }
  function closeSettings() {
    setSettings(null)
  }
  function goToAsr() {
    setVoiceInstall(false)
    openSettings('resources', 'asr')
  }
  function openDownload(id) {
    closeSettings()
    downloadPageOpen.current = true
    stage.current?.setVisible(false)
    setResource(id)
  }
  function closeDownload() {
    downloadPageOpen.current = false
    setResource(null)
    stage.current?.setVisible(hostVisibility.current && !document.hidden)
  }
  function backToResources() {
    closeDownload()
    openSettings('resources', resource)
  }
  async function confirmDelete() {
    const done = await flow.deleteChat(deleting)
    if (done) setDeleting(null)
    return done
  }
  const missing = state.chats.filter(
    (chat) => chat.mode === state.target.tab && chat.participantIssue
  )
  const theatre = state.chats.filter(
    (chat) => chat.mode === 'scripted-dialogue' && !chat.participantIssue
  )
  return (
    <>
      <main className="home-page" aria-label="数字小祥主页" inert={Boolean(resource)}>
        <aside className="home-sidebar">
          <div className="home-brand">
            <img src={logo} alt="" width="40" height="40" />
            <img src={wordmark} alt="数字小祥" width="132" height="38" />
          </div>
          <div className="home-tabs" role="tablist" aria-label="对话类型">
            <button
              role="tab"
              aria-selected={state.target.tab === 'single-character'}
              onClick={dialogueTab}
            >
              对话
            </button>
            <button
              role="tab"
              aria-selected={state.target.tab === 'scripted-dialogue'}
              onClick={theatreTab}
            >
              小剧场
            </button>
          </div>
          <nav className="home-navigation" aria-label="角色与对话">
            {state.loading && <p className="home-empty">正在读取对话…</p>}
            {state.target.tab === 'single-character' && !state.loading && (
              <CharacterGroups state={state} flow={flow} client={client} onDelete={setDeleting} />
            )}
            {state.target.tab === 'scripted-dialogue' && (
              <>
                <Button variant="ghost" disabled>
                  <Plus size={14} />
                  新建小剧场
                </Button>
                {theatre.map((chat) => (
                  <ChatItem
                    key={chat.id}
                    chat={chat}
                    selected={state.target.chatId === chat.id}
                    flow={flow}
                    onDelete={setDeleting}
                  />
                ))}
                {!theatre.length && <p className="home-empty">还没有小剧场记录</p>}
              </>
            )}
            {missing.length > 0 && (
              <section className="missing-chats">
                <h2>角色缺失的对话</h2>
                {missing.map((chat) => (
                  <ChatItem
                    key={chat.id}
                    chat={chat}
                    selected={state.target.chatId === chat.id}
                    flow={flow}
                    onDelete={setDeleting}
                  />
                ))}
              </section>
            )}
          </nav>
          <div className="home-sidebar-footer">
            <NoticeStack
              notices={state.notices}
              onDismiss={flow.dismiss}
              onSettings={openSettings}
              setup={setup}
              setupState={setupState}
            />
            <Button variant="ghost" className="home-settings" onClick={() => openSettings()}>
              <Settings size={16} />
              设置
            </Button>
          </div>
        </aside>
        <section className="home-stage" aria-label="角色舞台">
          <div
            className="home-canvas"
            ref={canvas}
            data-stage-state={stageState.loading ? 'loading' : 'ready'}
          />
          <div className={stageState.text ? 'stage-scrim active' : 'stage-scrim'} />
          {stageState.loading && (
            <div className="home-stage-status" role="status">
              正在加载…
            </div>
          )}
          {stageState.problem && (
            <div className="home-stage-status" role="alert">
              {stageState.problem}
            </div>
          )}
          {setupState.connectionProblem && (
            <div className="home-connection" role="status">
              {setupState.connectionProblem}
            </div>
          )}
          <Toolbar onZoom={zoomModel} onReset={resetModel} />
          <div className="home-bottom">
            <Subtitle state={stageState} />
            <Composer
              state={state}
              flow={flow}
              asrReady={Boolean(setupState.features?.asrReady)}
              onVoiceInstall={() => setVoiceInstall(true)}
              onConfigure={() => openSettings()}
              onSend={sendMessage}
              onStopReplay={
                !historyOpen && currentReplay && !currentReplay.error ? stopHistoryTurn : null
              }
            />
          </div>
          {historyOpen ? (
            <HistoryPanel
              key={state.target.chatId ?? 'blank'}
              snapshot={state.snapshot}
              characters={state.characters}
              onClose={hideHistory}
              onPlayAudio={playHistoryAudio}
              onPlayTurn={playHistoryTurn}
              turnReplay={currentReplay}
            />
          ) : (
            <Button
              className="history-handle"
              variant="ghost"
              aria-label="展开聊天记录"
              onClick={showHistory}
            >
              <ChevronLeft />
            </Button>
          )}
          {state.target.tab === 'scripted-dialogue' && !historyOpen && state.target.chatId && (
            <Button className="read-history" onClick={showHistory}>
              <MessageCircle />
              查看小剧场记录
            </Button>
          )}
        </section>
        {deleting && (
          <DeleteDialog chat={deleting} onCancel={cancelDelete} onConfirm={confirmDelete} />
        )}
        {voiceInstall && (
          <VoiceInstallDialog onCancel={() => setVoiceInstall(false)} onConfirm={goToAsr} />
        )}
        {settings && (
          <SettingsPanel
            entry={settings}
            client={client}
            flow={flow}
            state={state}
            setup={setup}
            setupState={setupState}
            onClose={closeSettings}
            onDownload={openDownload}
          />
        )}
      </main>
      {resource && (
        <ResourceDownloadPage
          key={resource}
          target={resource}
          client={client}
          setup={setup}
          state={setupState}
          onBack={backToResources}
          onReturn={closeDownload}
        />
      )}
    </>
  )
}
