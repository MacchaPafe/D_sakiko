import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore
} from 'react'
import {
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Download,
  Pencil,
  Plus,
  Settings,
  Square,
  Theater,
  Trash2,
  Users,
  Volume2
} from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { DialogNavigator } from '../../components/ui/DialogNavigator.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { Tabs, TabList, Tab, TabPanel } from '../../components/ui/segmented-tabs.jsx'
import { useDialogNavigation } from '../../components/ui/use-dialog-navigation.js'
import { isReplayableDialogue, lastDialogueEntry } from '../../../../shared/chat-history.js'
import logo from '../../assets/welcome/logo.svg'
import wordmark from '../../assets/welcome/wordmark.svg'
import { desktopHost } from '../../host/desktop.js'
import { CharacterAvatar } from './CharacterAvatar.jsx'
import { HomeFlow, hasConnection, selectedModel } from './home-flow.js'
import { StageController, SubtitleProgress } from './stage-controller.js'
import { resolveTheatreStageState } from './theatre-stage.js'
import { TheatreActions, TheatreSetup } from './TheatreSetup.jsx'
import { TheatrePositions } from './TheatrePositions.jsx'
import { StageBackdrop, StageBackgroundPreview, StageResources } from './BackgroundGallery.jsx'
import { StoryCaption, StoryRail, StoryLog } from './StoryStage.jsx'
import { StageToolbar } from './StageToolbar.jsx'
import { ToolMenu, ThinkingPicker, ThinkingConflict } from './ToolControls.jsx'
import { InteractionCards } from './InteractionCards.jsx'
import { useRevealClock } from './use-reveal-clock.js'
import { AttachmentList, AddAttachments } from './AttachmentList.jsx'
import { PlayingSubtitle, SubtitleText } from './DialogueMarkdown.jsx'
import { HistoryPanel } from './HistoryPanel.jsx'
import { NoticeStack } from './NoticeStack.jsx'
import { DownloadCenter } from './DownloadCenter.jsx'
import { useDownloadJobs } from './download-jobs.js'
import { DeleteDialog } from './DeleteDialog.jsx'
import { CharacterManager } from '../characters/CharacterManager.jsx'
import { IdentityButton, PersonaPicker } from '../personas/PersonaPicker.jsx'
import { OutfitManager } from './OutfitManager.jsx'
import { VoiceInput, VoiceInstallDialog } from './VoiceInput.jsx'
import { SettingsPanel } from '../settings/SettingsPanel.jsx'
import { ResourceDownloadConfirmation } from '../settings/Resources.jsx'
import {
  characterSidebarGroupVars,
  characterStageThemeVars,
  resolveCharacterThemeColor
} from '../../styles/character-theme.js'
import './home.css'

const storyControls =
  'button, a, input, textarea, select, label, summary, [contenteditable]:not([contenteditable="false"]), [role="button"], [role="menu"], [role="dialog"], [popover], .home-composer, .story-rail'

/** @typedef {{kind: 'characters', initialId?: string, focusAffiliation?: boolean, initialTab?: 'roles'|'personas', initialPersonaId?: string} | {kind: 'settings', entry: {section: string, target?: string}} | {kind: 'resource', target: string, formId?: string} | {kind: 'outfits', target: {characterId: string, formId: string, tab?: string, section?: string, backgroundTab?: string}} | {kind: 'theatre', chat: import('../../../../shared/contracts/conversation.js').Chat|null} | {kind: 'downloads', category: 'all'|'resources'|'outfits'|'backgrounds'} | {kind: 'outfit-detail', render: (onClose: () => void) => import('react').ReactNode}} HomeDialogContent */
/** @typedef {HomeDialogContent & import('../../components/ui/use-dialog-navigation.js').DialogPage} HomeDialogPage */

function downloadPage(category) {
  return { kind: 'downloads', title: '下载中心', layout: 'download-center', category }
}

function ChatItem({
  chat,
  selected,
  flow,
  onDelete,
  roles,
  client,
  liveReply,
  preferChinese = true
}) {
  function select() {
    flow.selectChat(chat)
  }
  function remove() {
    onDelete(chat)
  }
  const now = useRevealClock(chat.lastReplyRevealAt ? [{ revealAt: chat.lastReplyRevealAt }] : [])
  const original = liveReply?.line.text ?? chat.lastReply
  const translation = liveReply ? liveReply.translation : chat.lastReplyTranslation
  let preview = (preferChinese && translation) || original
  if (!liveReply && chat.lastReplyRevealAt > now) preview = '正在抽签'
  const theatre = chat.mode === 'scripted-dialogue'
  const actors = theatre
    ? (chat.characterIds ?? []).map(
        (id) =>
          roles?.find((item) => item.id === id) ?? {
            id,
            displayName: chat.characterNames?.[id] ?? id
          }
      )
    : []
  const rowClass =
    'home-chat-row' + (theatre ? ' theatre-chat-row' : '') + (selected ? ' selected' : '')
  return (
    <div className={rowClass}>
      <button onClick={select} aria-current={selected ? 'page' : undefined}>
        {theatre ? (
          <>
            <span className="theatre-chat-avatars" aria-hidden="true">
              {actors.slice(0, 2).map((actor) => (
                <CharacterAvatar
                  key={actor.id}
                  role={actor}
                  client={client}
                  size={26}
                  className="theatre-chat-avatar"
                />
              ))}
            </span>
            <span className="theatre-chat-meta">
              <span className="theatre-chat-title">{chat.title}</span>
              <span className="theatre-chat-preview">{preview || '尚未生成剧本'}</span>
            </span>
          </>
        ) : (
          preview || '暂无回复'
        )}
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
function CharacterGroup({
  role,
  activeFormId,
  state,
  flow,
  client,
  expanded,
  onToggle,
  onDelete,
  onAffiliation
}) {
  const active = state.target.characterId === role.id
  const listId = useId()
  const unconfirmed = role.bangDreamId === undefined
  const groupThemeColor = resolveCharacterThemeColor(role, active ? activeFormId : null)
  const groupThemeStyle = characterSidebarGroupVars(groupThemeColor)
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
  function affiliation() {
    onAffiliation(role)
  }
  return (
    <section
      className="home-role-group"
      style={groupThemeStyle}
      data-has-role-theme={groupThemeStyle ? 'true' : undefined}
    >
      <div className="home-role-header">
        <button
          className={'home-role' + (expanded ? ' selected' : '')}
          aria-current={active ? 'true' : undefined}
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={toggle}
        >
          <CharacterAvatar role={role} client={client} />
          <span className="home-role-name">{role.name ?? role.displayName}</span>
          <ChevronRight className="home-role-chevron" size={16} aria-hidden="true" />
        </button>
        <Button
          className="home-role-affiliation"
          variant="ghost"
          size="icon-xs"
          data-unconfirmed={unconfirmed || undefined}
          aria-label={
            (unconfirmed ? '确认角色归属：' : '编辑角色：') + (role.name ?? role.displayName)
          }
          title={unconfirmed ? '确认角色归属' : '编辑角色'}
          onClick={affiliation}
        >
          {unconfirmed ? (
            <span className="home-role-unconfirmed-badge">待关联</span>
          ) : (
            <Pencil size={14} />
          )}
        </Button>
      </div>
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
                preferChinese={state.settings?.display?.preferChinesePreview}
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

function CharacterGroups({
  state,
  activeFormId,
  flow,
  client,
  expandedRoleId,
  onExpandChange,
  onDelete,
  onAffiliation
}) {
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
    onExpandChange((current) => {
      const resolved = current === undefined ? state.target.characterId : current
      return resolved === roleId ? null : roleId
    })
  }

  return state.characters.map((role) => (
    <CharacterGroup
      key={role.id}
      role={role}
      activeFormId={activeFormId}
      state={state}
      flow={flow}
      client={client}
      expanded={expandedRoleId === role.id}
      onToggle={toggleRole}
      onDelete={onDelete}
      onAffiliation={onAffiliation}
    />
  ))
}
function Subtitle({ state, subtitles, replayingTurn, onToggleReplay, theatre }) {
  const replaying = state.replayingLine || replayingTurn
  if (!state.text && !replaying) return <div className="home-subtitle" />
  let replayLabel = '回放当句对话'
  if (replayingTurn) replayLabel = theatre ? '停止整场回放' : '停止本轮回放'
  else if (state.replayingLine) replayLabel = '停止当句回放'
  return (
    <section className="home-subtitle" aria-label="当前台词">
      <div className="subtitle-name">
        <span>{state.name}</span>
        {(state.canReplayLine || replaying) && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className="subtitle-replay"
            aria-label={replayLabel}
            onClick={onToggleReplay}
          >
            {replaying ? <Square /> : <Volume2 />}
          </Button>
        )}
      </div>
      <div className="subtitle-body">
        <div className="subtitle-text">
          <PlayingSubtitle text={state.text} progress={subtitles} />
        </div>
        {state.text && state.translation && (
          <div className="subtitle-translation">
            <SubtitleText text={state.translation} />
          </div>
        )}
        {state.lineReplayError && (
          <div className="subtitle-replay-error" role="alert">
            {state.lineReplayError}
          </div>
        )}
      </div>
    </section>
  )
}
function ModelPicker({ state, flow, onConfigure }) {
  if (!state.connections.some((item) => item.models.length)) return null
  const selection = selectedModel(state)
  function change(value) {
    if (value) void flow.chooseModel(JSON.parse(value))
  }
  const options = state.connections.flatMap((connection) =>
    connection.models.map((model) => ({
      value: JSON.stringify({ connectionId: connection.id, modelId: model.id }),
      label: model.id,
      group: connection.name
    }))
  )
  return (
    <div className="home-model-picker">
      <SelectField
        label="对话模型"
        options={options}
        placeholder="选择模型"
        value={hasConnection(state) && selection ? JSON.stringify(selection) : ''}
        onChange={change}
        footer={{ label: '管理模型', onAction: onConfigure }}
      />
    </div>
  )
}

function buildModelControl(state, flow, onConfigure) {
  const hasAvailableModels = state.connections.some((item) => item.models.length > 0)
  if (hasAvailableModels) return <ModelPicker state={state} flow={flow} onConfigure={onConfigure} />
  if (!hasConnection(state)) {
    return (
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
  return null
}

function findReplayTurnId(snapshot) {
  if (!snapshot || snapshot.activeTurnId) return null
  const turns = snapshot.chat.turns ?? []
  const ordered = snapshot.chat.mode === 'scripted-dialogue' ? turns : [...turns].reverse()
  for (const turn of ordered) {
    for (const message of turn.messages ?? []) {
      if (message.kind !== 'assistant') continue
      for (const part of message.parts ?? []) {
        if (part.kind === 'dialogue' && part.lines.some(isReplayableDialogue)) return turn.id
      }
    }
  }
  return null
}

const replyPhaseLabels = {
  generation: '正在生成回复…',
  speech: '正在合成语音…',
  presentation: '正在播放回复…'
}

function Composer({
  identityControl,
  state,
  flow,
  asrReady,
  onVoiceInstall,
  onConfigure,
  onSend,
  compact,

  onFocus
}) {
  const textarea = useRef(null)
  const configured = hasConnection(state)
  const readonly =
    state.target.tab === 'scripted-dialogue' ||
    Boolean(state.snapshot?.participantIssue) ||
    !state.target.characterId
  const active = Boolean(state.snapshot?.activeTurnId)
  const files = flow.input().attachments
  const conflict = flow.reasoningState().conflict
  const canSend =
    configured &&
    !readonly &&
    !state.sending &&
    !active &&
    !conflict &&
    (Boolean(state.draft.trim()) || files.items.length > 0) &&
    files.items.every((f) => f.state === 'ready') &&
    (!state.target.chatId || state.snapshot?.admission.canGenerate)

  useEffect(() => {
    if (!textarea.current) return
    textarea.current.style.height = 'auto'
    textarea.current.style.height =
      Math.min(144, Math.max(28, textarea.current.scrollHeight)) + 'px'
  }, [state.draft, compact, textarea])
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
  const modelControl = readonly ? null : buildModelControl(state, flow, onConfigure)
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
    <div className="home-composer-group" data-compact={compact || undefined}>
      {compact && active && (
        <button className="story-live-status" onClick={flow.stop} title="停止生成">
          {status ?? '正在回复…'} <Square size={12} /> 停止
        </button>
      )}
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
      <ThinkingConflict flow={flow} />
      <AttachmentList draft={files} disabled={state.sending} />
      <div className="home-composer">
        <textarea
          ref={textarea}
          onFocus={onFocus}
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
            <AddAttachments draft={files} disabled={readonly || state.sending} />
            <ToolMenu
              enabled={flow.enabledTools()}
              onChange={(names) => flow.setTools(names)}
              active={active}
              disabled={readonly || state.sending}
            />
            {identityControl}
            {status && (
              <span className="composer-status" role="status">
                {status}
              </span>
            )}
          </div>
          <div className="home-composer-actions">
            {modelControl}
            {!readonly && <ThinkingPicker flow={flow} />}
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

/** @param {{client: import('../../backend/client.js').BackendClient, setup: object, setupState: object, startupPending: boolean, onStartupReady: () => void}} props 应用连接、后台任务和启动封面。 @returns {import('react').ReactElement} 正式主页，历史悬浮于稳定舞台之上。 */
export function HomePage({ client, setup, setupState, startupPending, onStartupReady }) {
  const [subtitles] = useState(() => new SubtitleProgress())
  const [flow] = useState(() => new HomeFlow(client))
  const state = useSyncExternalStore(flow.subscribe, flow.getSnapshot)
  const [story, setStory] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [auto, setAuto] = useState(true)
  const [writing, setWriting] = useState(false)
  function messageInput() {
    return stageElement.current?.querySelector('.home-composer textarea')
  }
  const stageElement = useRef(null)
  const previousStageCenter = useRef(null)
  const stagePress = useRef(null)
  const [toolAnchor, setToolAnchor] = useState(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [expandedRoleId, setExpandedRoleId] = useState(undefined)
  const [turnReplay, setTurnReplay] = useState(null)
  const [branchLink, setBranchLink] = useState(null)
  const [deleting, setDeleting] = useState(null)
  /** @type {ReturnType<typeof useDialogNavigation<HomeDialogPage>>} */
  const { frames, open, push, replace, dismiss } = useDialogNavigation()
  const rootDialog = frames[0]?.page.kind
  const activeDialog = frames.at(-1)?.page.kind
  const [voiceInstall, setVoiceInstall] = useState(false)
  const [backgroundTarget, setBackgroundTarget] = useState(null)
  const [pickerPreview, setPickerPreview] = useState(null)
  const [galleryPreview, setGalleryPreview] = useState(null)
  const closeBackground = useCallback(() => {
    setBackgroundTarget(null)
    setPickerPreview(null)
  }, [])
  let currentBackground = state.draftBackground
  if (state.target.chatId)
    currentBackground = state.snapshot
      ? state.snapshot.chat.meta.settings.presentation.background
      : state.restoredStage?.background
  const backgroundKnown = state.target.chatId
    ? Boolean(state.snapshot || state.restoredStage)
    : !state.loading
  const [decodedBackground, setDecodedBackground] = useState(undefined)
  const backgroundId = currentBackground?.id ?? null
  const stageCovered = Boolean(deleting || voiceInstall || (story && historyOpen))
  const backgroundSuspended = Boolean(rootDialog || stageCovered)
  let stagePreview = null
  if (!stageCovered && activeDialog === 'outfits') stagePreview = galleryPreview
  else if (!backgroundSuspended && backgroundTarget === state.target) stagePreview = pickerPreview
  const [wardrobeActor, setWardrobeActor] = useState(null)
  const [stageEditor, setStageEditor] = useState(null)
  const [replaceTarget, setReplaceTarget] = useState(null)
  const isTheatre = state.target.tab === 'scripted-dialogue'
  const theatreChat = isTheatre && !state.snapshot?.participantIssue ? state.snapshot?.chat : null
  const editor = theatreChat && stageEditor?.target === state.target ? stageEditor : null
  const theatreRoles = (theatreChat?.characterIds ?? [])
    .map((id) => state.characters.find((item) => item.id === id))
    .filter(Boolean)
  const [stageState, setStageState] = useState({
    text: '',
    name: '',
    guideId: null,
    canReplayLine: false,
    replayingLine: false,
    lineReplayError: null,
    problem: null,
    loading: false,
    hasModel: false,
    modelsSettled: false,
    canRetryModel: false,
    shownOutfitKey: null,
    failedOutfitKey: null,
    sides: null
  })
  const theatreStage = isTheatre
    ? resolveTheatreStageState({
        actors: theatreRoles,
        sides: stageState.sides,
        orientation: theatreChat?.meta.settings.presentation.orientation,
        requestedPanel: editor?.panel ?? null
      })
    : null
  const canvas = useRef(null)
  const stage = useRef(null)
  const stageTarget =
    state.loading && !state.target.chatId && !state.target.characterId ? null : state.target
  const sequenceId = state.snapshot?.presentation.sequenceId ?? null
  const draftPresentation = state.draftOutfit?.model?.presentation
  const currentReplay = turnReplay?.chatId === state.target.chatId ? turnReplay : null
  const downloadsOpen = activeDialog === 'downloads'
  const downloads = useDownloadJobs(client, {
    outfits: downloadsOpen || frames.some(({ page }) => page.kind === 'outfits'),
    backgrounds: downloadsOpen || frames.some(({ page }) => page.kind === 'outfits'),
    resources: downloadsOpen
  })
  const downloadCount = downloads.count
  useEffect(() => {
    void flow.mount()
    return () => flow.dispose()
  }, [flow])
  useEffect(() => {
    if (startupPending || deleting || voiceInstall || rootDialog || (story && historyOpen)) return
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
      if (!event.repeat)
        open({
          kind: 'settings',
          title: '设置',
          layout: 'settings-dialog',
          entry: { section: 'connections' }
        })
    }
    window.addEventListener('keydown', handleSettingsShortcut)
    return () => window.removeEventListener('keydown', handleSettingsShortcut)
  }, [startupPending, deleting, voiceInstall, rootDialog, open, story, historyOpen])
  useEffect(() => {
    const controller = new StageController(client, setStageState, subtitles)
    stage.current = controller
    controller.mount(canvas.current)
    const host = desktopHost()
    let active = true
    let visible = true
    function sync() {
      if (active) controller.setVisible(visible && !document.hidden)
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
  }, [client, subtitles])
  useEffect(() => {
    if (!stageTarget) return
    if (stage.current)
      stage.current.player.suppressScript = isTheatre && flow.liveTheatre !== stageTarget
    void stage.current?.select(
      stageTarget.characterId,
      stageTarget.chatId,
      sequenceId,
      draftPresentation,
      isTheatre,
      state.draftOutfit?.formId
    )
  }, [stageTarget, flow, sequenceId, draftPresentation, isTheatre, state.draftOutfit?.formId])
  const now = useRevealClock(state.snapshot?.chat.meta.lotteries)
  const revealPending =
    state.snapshot?.chat.meta.lotteries?.some((item) => item.revealAt > now) ?? false
  const restoredSubtitle = state.restoredStage?.subtitle ?? null
  useEffect(() => {
    stage.current?.setHistory(state.snapshot, restoredSubtitle)
  }, [state.snapshot, restoredSubtitle, revealPending])
  useEffect(() => {
    if (
      startupPending &&
      !state.loading &&
      stageState.modelsSettled &&
      backgroundKnown &&
      decodedBackground === backgroundId
    ) {
      onStartupReady()
    }
  }, [
    startupPending,
    state.loading,
    stageState.modelsSettled,
    backgroundKnown,
    decodedBackground,
    backgroundId,
    onStartupReady
  ])
  function retryStageModel() {
    stage.current?.retryModel(
      isTheatre ? theatreRoles.findIndex((item) => item.id === role?.id) : undefined
    )
  }
  function chooseStageOutfit(formId, model, characterId = role?.id) {
    stage.current?.stopReplay()
    return flow.chooseOutfit(formId, model, characterId)
  }
  async function chooseStageForm(formId) {
    stage.current?.stopReplay()
    const current = flow.getSnapshot()
    if (current.sending) return
    const { target } = current
    if (target.chatId) {
      await client.conversations.patchSettings(target.chatId, {
        characters: { [target.characterId]: { formId } }
      })
      return
    }
    await flow.chooseForm(formId)
  }
  function zoomModel(factor) {
    const index = editor?.selected ?? 0
    stage.current?.zoom(factor, index)
    if (isTheatre) saveFrame(index)
  }
  function resetModel() {
    const index = editor?.selected ?? 0
    stage.current?.resetLayout(index)
    if (isTheatre) saveFrame(index)
  }
  function report(error) {
    if (flow.getSnapshot().target === state.target) flow.update({ problem: error.message })
  }
  function saveFrame(index) {
    void stage.current?.saveFrame(index).catch(report)
  }
  function setPanel(panel) {
    if (panel) setHistoryOpen(false)
    // 当前服装不支持面对面时不允许停留在「朝向」页签，统一落回站位面板。
    const nextPanel = panel === 'orientation' && !theatreStage?.canFace ? 'position' : panel
    setStageEditor(
      nextPanel
        ? { target: state.target, panel: nextPanel, selected: editor?.selected ?? null }
        : null
    )
  }
  function chooseSide(selected) {
    setStageEditor({ target: state.target, panel: 'position', selected })
  }
  function face(orientation) {
    void client.conversations
      .patchSettings(state.target.chatId, { presentation: { orientation } })
      .catch(report)
  }
  function setupTheatre(chat) {
    setReplaceTarget(null)
    open({
      kind: 'theatre',
      title: chat ? '剧场设定' : '新建小剧场',
      layout: 'theatre-settings-dialog',
      chat
    })
  }
  function generateTheatre() {
    if (theatreChat.turns.length && replaceTarget !== state.target) {
      setReplaceTarget(state.target)
      return
    }
    setReplaceTarget(null)
    stage.current?.stopReplay()
    if (stage.current) stage.current.player.suppressScript = false
    void flow.generateTheatre()
  }
  function showHistory() {
    if (story) {
      setToolsOpen(false)
      stage.current?.setReadingLog(true)
    }
    setStageEditor(null)
    closeBackground()
    setHistoryOpen(true)
  }
  function hideHistory() {
    if (story) stage.current?.setReadingLog(false)
    setHistoryOpen(false)
  }
  function playHistoryAudio(audio, signal, onStop) {
    const controller = stage.current
    if (!controller) return onStop()
    if (!story) controller.stopReplay()
    controller.playHistoryAudio(audio, signal, onStop)
  }
  function toggleStageReplay() {
    void stage.current?.replayLine(state.target.chatId)
  }
  function playHistoryTurn(turnId) {
    if (currentReplay?.id === turnId && !currentReplay.error) {
      stage.current?.stopReplay()
      return
    }
    startHistoryTurn(turnId)
  }
  function replayFromStoryLog(turnId) {
    // 先替换记录页下方暂停的回放，避免关页时旧台词短暂续播。
    startHistoryTurn(turnId)
    hideHistory()
    requestAnimationFrame(() => stageElement.current?.focus())
  }
  function startHistoryTurn(turnId) {
    const chatId = state.target.chatId
    const controller = stage.current
    if (!chatId || !controller) return
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
    return flow.send((chatId) => {
      stage.current?.stopReplay(chatId)
      if (!flow.input().text) setWriting(false)
    })
  }
  function changeStageLayout(expanded) {
    const rect = stageElement.current.getBoundingClientRect()
    previousStageCenter.current = rect.left + rect.width / 2
    setStory(expanded)
  }

  function enterStory() {
    setWriting(
      Boolean(
        state.draft ||
        flow.input().attachments.items.length ||
        document.activeElement === messageInput() ||
        document.querySelector('.home-voice-input [aria-label="取消语音输入"]')
      )
    )
    setHistoryOpen(false)
    closeBackground()
    setStageEditor(null)
    changeStageLayout(true)
    if (document.activeElement !== messageInput()) stageElement.current?.focus()
  }

  function exitStory() {
    stage.current?.player.setReplayAuto(true)
    stage.current?.setReadingLog(false)
    for (const popup of document.querySelectorAll(':popover-open')) popup.hidePopover()
    changeStageLayout(false)
    setRailOpen(false)
    setToolsOpen(false)
    setHistoryOpen(false)
    setWriting(false)
    closeBackground()
    setStageEditor(null)
    setVoiceInstall(false)
    setReplaceTarget(null)
    for (const frame of [...frames].reverse()) dismiss(frame.id)
    requestAnimationFrame(() => {
      if (document.activeElement !== messageInput())
        stageElement.current?.querySelector('[aria-label="全窗口舞台"]')?.focus()
    })
  }

  function toggleRail() {
    setRailOpen(!railOpen)
    setToolsOpen(false)
    closeBackground()
    setStageEditor(null)
  }

  function toggleEditor() {
    setWriting(!editorVisible)
    if (!editorVisible) requestAnimationFrame(() => messageInput()?.focus())
  }

  function openStageTools() {
    setToolsOpen(!toolsOpen)
    closeBackground()
    setStageEditor(null)
  }

  function toggleStoryReplay() {
    if (replaying) return stage.current?.stopReplay()
    setToolsOpen(false)
    closeBackground()
    setStageEditor(null)
    if (replayTurnId) playHistoryTurn(replayTurnId)
  }

  function toggleStoryAuto(event) {
    setAuto(!auto)
    // 鼠标切换后让空格继续用于翻句；键盘操作工具栏时保留按钮焦点。
    if (event.detail) stageElement.current?.focus()
  }

  function advanceStory() {
    stage.current?.player.advanceReplay()
  }

  function startStagePress(event) {
    stagePress.current = null
    if (
      event.button !== 0 ||
      !event.isPrimary ||
      event.target.closest(storyControls) ||
      document.querySelector(':popover-open')
    )
      return
    stagePress.current = { id: event.pointerId, x: event.clientX, y: event.clientY }
  }

  function trackStagePress(event) {
    const press = stagePress.current
    // 画布会捕获指针，拖动结束时也会收到 click；即使拖回原点也不能翻句。
    if (
      press?.id === event.pointerId &&
      Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4
    )
      stagePress.current = null
  }

  function clickStoryStage(event) {
    const press = stagePress.current
    stagePress.current = null
    if (
      press &&
      event.button === 0 &&
      !event.defaultPrevented &&
      Math.hypot(event.clientX - press.x, event.clientY - press.y) <= 4 &&
      !event.target.closest(storyControls) &&
      !window.getSelection()?.toString()
    )
      advanceStory()
  }

  function cancelDelete() {
    setDeleting(null)
  }
  function openSettings(section = 'connections', target) {
    open({ kind: 'settings', title: '设置', layout: 'settings-dialog', entry: { section, target } })
  }
  function goToAsr() {
    setVoiceInstall(false)
    openSettings('resources', 'asr')
  }
  function openDownload(target, title, formId) {
    push({ kind: 'resource', target, title, formId, layout: 'outfit-detail-dialog' })
  }
  function resourceDownloadStarted(id) {
    replace(id, downloadPage('resources'))
  }
  function showOutfits(target) {
    open({ kind: 'outfits', title: '舞台资源', layout: 'outfit-manager', target })
  }
  function toggleBackgroundPicker() {
    if (backgroundTarget === state.target) return closeBackground()
    const selectedActor = theatreRoles[editor?.selected]
    if (selectedActor) setWardrobeActor({ target: state.target, id: selectedActor.id })
    setBackgroundTarget(state.target)
  }
  function showBackgrounds(backgroundTab = 'online') {
    const actor = theatreRoles[editor?.selected] ?? role ?? state.characters[0]
    const formId =
      state.snapshot?.chat.meta.settings.characters[actor?.id]?.formId ??
      (isTheatre ? null : state.draftOutfit?.formId) ??
      actor?.defaultFormId
    showOutfits({ characterId: actor?.id, formId, section: 'backgrounds', backgroundTab })
  }
  async function applyManagedBackground(asset) {
    await flow.chooseBackground(asset)
    closeBackground()
    for (const frame of [...frames].reverse()) dismiss(frame.id)
  }
  function openOutfits(formId) {
    showOutfits({ characterId: role.id, formId })
  }
  function showOutfitDetail(title, render) {
    push({ kind: 'outfit-detail', title, layout: 'outfit-detail-dialog', render })
  }
  function refreshStageOutfit() {
    const current = flow.getSnapshot()
    if (!current.target.chatId && !current.draftOutfit)
      void stage.current?.select(current.target.characterId, null, null)
  }
  function dismissDialog(id) {
    if (
      frames.length === 1 &&
      frames[0].id === id &&
      ['outfits', 'characters'].includes(rootDialog)
    )
      refreshStageOutfit()
    dismiss(id)
  }
  function showDownloads() {
    open(downloadPage('all'))
  }
  function showStageDownloads(section) {
    push(downloadPage(section))
  }
  function showResourceDownloads() {
    push(downloadPage('resources'))
  }
  function showStorageDownloads() {
    push(downloadPage('all'))
  }
  function manageStoredCharacter(id) {
    push({
      kind: 'characters',
      title: '角色管理',
      layout: 'character-manager-dialog',
      initialId: id
    })
    void setup.refreshManagedCatalog()
  }
  function viewInstalledOutfit(target) {
    showOutfits({ ...target, tab: 'installed' })
  }
  function viewInstalledResource(target) {
    if (rootDialog === 'outfits') refreshStageOutfit()
    openSettings('resources', target)
  }
  async function tryManagedOutfit(characterId, formId, model, onClose) {
    if (characterId !== role?.id) return
    await chooseStageOutfit(formId, model, characterId)
    onClose()
  }
  function removeDraftOutfit(target, modelId) {
    flow.invalidateDraftOutfit(target.characterId, target.formId, modelId)
  }
  function refreshAffiliation() {
    void flow.refresh().catch(() => {})
  }
  function openCharacters(selected) {
    open({
      kind: 'characters',
      title: '角色管理',
      layout: 'character-manager-dialog',
      initialId: selected?.id ?? state.target.characterId,
      focusAffiliation: Boolean(selected && selected.bangDreamId === undefined)
    })
    void setup.refreshManagedCatalog()
  }
  function openAffiliation(role) {
    openCharacters(role)
  }
  function openIdentity() {
    if (!state.target.chatId) void flow.refreshPersonas()
    open({ kind: 'persona-picker', title: '我的身份', layout: 'persona-picker-dialog' })
  }
  function manageIdentities(initialPersonaId) {
    push({
      kind: 'characters',
      title: '角色管理',
      layout: 'character-manager-dialog',
      initialTab: 'personas',
      initialPersonaId,
      initialId: state.target.characterId
    })
    if (initialPersonaId) void flow.refreshPersonas()
    void setup.refreshManagedCatalog()
  }
  async function refreshCharacters(deletedId) {
    await flow.refresh()
    const current = flow.getSnapshot()
    if (deletedId && !current.target.chatId && current.target.characterId === deletedId)
      flow.newChat(current.characters[0]?.id ?? null)
    void setup.refreshManagedCatalog({ localChanged: true })
    void setup.refreshFeatures()
  }
  function editOutfits(characterId, formId, tab) {
    push({
      kind: 'outfits',
      title: '舞台资源',
      layout: 'outfit-manager',
      target: { characterId, formId, tab }
    })
  }
  async function confirmDelete() {
    const done = await flow.deleteChat(deleting)
    if (done) setDeleting(null)
    return done
  }
  const role = isTheatre
    ? (theatreRoles.find(
        (item) => wardrobeActor?.target === state.target && item.id === wardrobeActor.id
      ) ?? theatreRoles[0])
    : state.characters.find((item) => item.id === state.target.characterId)
  // 初次展开恢复的角色，之后折叠状态只由侧栏操作决定，切换页签不重置。
  const activeExpandedRoleId =
    expandedRoleId === undefined ? state.target.characterId : expandedRoleId
  const selection = role && state.snapshot?.chat.meta.settings.characters[role.id]
  const activeForm = selection?.formId ?? state.draftOutfit?.formId ?? role?.defaultFormId
  // 仅单人模式随当前角色形态应用主题色；小剧场模式保持应用默认主色，避免双人切换时视觉跳变。
  const activeThemeColor = isTheatre ? null : resolveCharacterThemeColor(role, activeForm)
  const stageThemeStyle = characterStageThemeVars(activeThemeColor)
  const replayingTurn = Boolean(currentReplay && !currentReplay.error)
  const manualStage =
    story && replayingTurn && !auto && !historyOpen && !rootDialog && !voiceInstall && !deleting
  const replaying = replayingTurn || stageState.replayingLine
  const liveAuto = !replaying && Boolean(state.sending || state.snapshot?.activeTurnId)
  const stageLocked = story && replaying
  const editorVisible = writing || (!stageState.text && !state.snapshot?.activeTurnId)
  useLayoutEffect(() => {
    stage.current?.player.setStoryCaptions(story)
    const previous = previousStageCenter.current
    previousStageCenter.current = null
    if (previous === null || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const element = stageElement.current
    const rect = element.getBoundingClientRect()
    const offset = previous - (rect.left + rect.width / 2)
    // 画布一次调整到目标尺寸，仅在合成层平移角色，避免每帧重建 WebGL 缓冲。
    const timing = { duration: 220, easing: 'cubic-bezier(.2,.7,.25,1)' }
    const movement = canvas.current.animate(
      [{ translate: offset + 'px 0' }, { translate: '0 0' }],
      timing
    )
    const fade = element.animate([{ opacity: 0.78 }, { opacity: 1 }], timing)
    const sidebar = !story && element.parentElement.querySelector('.home-sidebar')
    const slide =
      sidebar &&
      sidebar.animate(
        [
          { opacity: 0.3, translate: '-12px 0' },
          { opacity: 1, translate: '0 0' }
        ],
        timing
      )
    return () => {
      movement.cancel()
      fade.cancel()
      if (slide) slide.cancel()
    }
  }, [story])
  useLayoutEffect(() => {
    stage.current?.player.setReplayAuto(!story || stageState.replayingLine || auto)
  }, [story, auto, stageState.replayingLine])

  useEffect(() => {
    if (!story) return
    function shortcut(event) {
      if (event.isComposing || event.keyCode === 229) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        exitStory()
      } else if (
        event.code === 'Space' &&
        !event.repeat &&
        !event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        manualStage &&
        !event.target.closest?.(storyControls)
      ) {
        event.preventDefault()
        advanceStory()
      }
    }
    window.addEventListener('keydown', shortcut, true)
    return () => window.removeEventListener('keydown', shortcut, true)
  })
  const canChooseOutfit =
    role &&
    (!isTheatre || theatreRoles.length === 2) &&
    (!state.target.chatId || (state.snapshot?.chat && !state.snapshot.participantIssue)) &&
    !historyOpen &&
    (!rootDialog || rootDialog === 'outfits') &&
    !voiceInstall &&
    !deleting
  const personaBusy = Boolean(
    isTheatre ||
    state.snapshot?.participantIssue ||
    !state.target.characterId ||
    stageState.replayingLine ||
    (currentReplay && !currentReplay.error) ||
    state.snapshot?.activeTurnId ||
    state.sending ||
    (state.target.chatId && state.snapshot?.activity !== 'idle')
  )
  const wardrobe = canChooseOutfit
    ? {
        role,
        client,
        chatSelection: selection,
        draftOutfit: state.draftOutfit,
        participants: isTheatre ? theatreRoles : null,
        ...(!isTheatre && {
          hasChat: Boolean(state.target.chatId),
          activeFormName: role.forms?.find((item) => item.id === activeForm)?.displayName ?? null,
          personaBusy,
          onChooseForm: chooseStageForm
        }),
        onActor: (id) => setWardrobeActor({ target: state.target, id }),
        stageState: isTheatre
          ? (stageState.actors?.[theatreRoles.indexOf(role)] ?? {})
          : stageState,
        savingDraft: !state.target.chatId && state.sending,
        onChoose: chooseStageOutfit,
        onManage: openOutfits,
        onRetryModel: retryStageModel
      }
    : null
  const stageTools = isTheatre
    ? {
        actors: theatreRoles,
        panel: theatreStage.panel,
        selected: editor?.selected,
        setPanel,
        face,
        canFace: theatreStage.canFace,
        inward: theatreStage.inward,
        degradedNote: theatreStage.degradedNote
      }
    : null
  const replayTurnId = useMemo(() => findReplayTurnId(state.snapshot), [state.snapshot])
  const liveTheatreReply = isTheatre ? lastDialogueEntry(state.snapshot?.chat, now) : null
  const theatreModelControl = theatreChat
    ? buildModelControl(state, flow, () => openSettings())
    : null
  const missing = state.chats.filter(
    (chat) => chat.mode === state.target.tab && chat.participantIssue
  )
  const theatre = state.chats.filter(
    (chat) => chat.mode === 'scripted-dialogue' && !chat.participantIssue
  )
  async function openBranch(id, original) {
    await flow.refreshChats()
    const branch = flow.getSnapshot().chats.find((chat) => chat.id === id)
    if (!branch || flow.getSnapshot().target.chatId !== original) return
    setBranchLink({ id, original })
    flow.selectChat(branch)
  }
  function returnFromBranch() {
    const original = state.chats.find((chat) => chat.id === branchLink?.original)
    if (original) flow.selectChat(original)
    setBranchLink(null)
  }
  function renderDialogPage(page, { id, onDismiss, returnTo }) {
    switch (page.kind) {
      case 'persona-picker':
        return (
          <PersonaPicker
            state={state}
            flow={flow}
            onClose={onDismiss}
            onManage={manageIdentities}
          />
        )
      case 'characters':
        return (
          <CharacterManager
            client={client}
            roles={state.characters}
            initialId={page.initialId}
            initialTab={page.initialTab}
            initialPersonaId={page.initialPersonaId}
            personas={state.personas}
            personaProblem={state.personaProblem}
            onRefreshPersonas={flow.refreshPersonas}
            focusAffiliation={page.focusAffiliation}
            active={activeDialog === 'characters'}
            completedJob={setupState.job?.status === 'succeeded' ? setupState.job.id : null}
            packages={
              setupState.managedCatalog?.groups.find((group) => group.id === 'voices')?.options
            }
            onRefresh={refreshCharacters}
            onClose={onDismiss}
            onOutfits={editOutfits}
            onChat={(characterId) => {
              setExpandedRoleId(characterId)
              flow.newChat(characterId)
              onDismiss()
              if (rootDialog === 'persona-picker') dismiss(frames[0].id)
            }}
            onPackage={(item, formId) =>
              openDownload(
                item.id,
                item.updateAvailable ? '查看语音包更新' : '使用推荐语音包',
                formId
              )
            }
          />
        )
      case 'theatre':
        return (
          <TheatreSetup
            client={client}
            flow={flow}
            roles={state.characters}
            chat={page.chat}
            personalityBusy={Boolean(
              page.chat &&
              (state.snapshot?.activity !== 'idle' ||
                stageState.replayingLine ||
                (currentReplay && !currentReplay.error))
            )}
            close={onDismiss}
          />
        )
      case 'settings':
        return (
          <SettingsPanel
            entry={page.entry}
            client={client}
            flow={flow}
            state={state}
            setup={setup}
            setupState={setupState}
            onClose={onDismiss}
            onDownload={openDownload}
            onShowDownloads={showResourceDownloads}
            onStorageDownloads={showStorageDownloads}
            onManageStoredCharacter={manageStoredCharacter}
          />
        )
      case 'outfits':
        return (
          <StageResources
            initial={page.target}
            client={client}
            jobs={downloads.backgrounds}
            outfitJobs={downloads.outfits}
            onClose={onDismiss}
            onDownloads={showStageDownloads}
            onApply={stageLocked ? null : applyManagedBackground}
            onPreview={setGalleryPreview}
            current={currentBackground}
            active={!stageCovered && id === frames.at(-1)?.id}
          >
            <OutfitManager
              client={client}
              roles={state.characters}
              initial={page.target}
              currentChat={
                !stageLocked &&
                role && {
                  characterId: role.id,
                  formId: activeForm,
                  modelId:
                    selection?.modelOverrides?.[activeForm] ?? state.draftOutfit?.model?.id ?? null
                }
              }
              onClose={onDismiss}
              onTry={(characterId, formId, model) =>
                tryManagedOutfit(characterId, formId, model, onDismiss)
              }
              onDeleted={removeDraftOutfit}
              jobs={downloads.outfits}
              onShowDetail={showOutfitDetail}
              onAffiliationSaved={refreshAffiliation}
            />
          </StageResources>
        )
      case 'downloads':
        return (
          <DownloadCenter
            client={client}
            setup={setup}
            downloads={downloads}
            initial={page.category}
            returnTo={returnTo}
            roles={state.characters}
            onClose={onDismiss}
            onOutfit={viewInstalledOutfit}
            onBackground={() => showBackgrounds('installed')}
            onResource={viewInstalledResource}
          />
        )
      case 'resource':
        return (
          <ResourceDownloadConfirmation
            title={page.title}
            target={page.target}
            formId={page.formId}
            client={client}
            setup={setup}
            onClose={onDismiss}
            onStarted={() => resourceDownloadStarted(id)}
          />
        )
      case 'outfit-detail':
        return page.render(onDismiss)
    }
  }
  return (
    <>
      <main
        className="home-page"
        data-story={story || undefined}
        aria-label="数字小祥主页"
        inert={startupPending}
      >
        <Tabs value={state.target.tab} onValueChange={flow.setTab} activationMode="manual" asChild>
          <aside className="home-sidebar">
            <div className="home-brand">
              <img src={logo} alt="" width="40" height="40" />
              <img src={wordmark} alt="数字小祥" width="132" height="38" />
            </div>
            <TabList className="home-tabs" aria-label="对话类型">
              <Tab value="single-character">对话</Tab>
              <Tab value="scripted-dialogue">小剧场</Tab>
            </TabList>
            <nav className="home-navigation" aria-label="角色与对话">
              {state.loading && (
                <p className="home-empty" role="status">
                  正在读取对话…
                </p>
              )}
              <TabPanel className="home-tab-panel" value="single-character">
                {!state.loading && (
                  <CharacterGroups
                    state={state}
                    activeFormId={isTheatre ? null : activeForm}
                    flow={flow}
                    client={client}
                    expandedRoleId={activeExpandedRoleId}
                    onExpandChange={setExpandedRoleId}
                    onDelete={setDeleting}
                    onAffiliation={openAffiliation}
                  />
                )}
              </TabPanel>
              <TabPanel className="home-tab-panel" value="scripted-dialogue">
                {!state.loading && (
                  <>
                    <Button
                      variant="ghost"
                      className="home-new-theatre"
                      onClick={() => setupTheatre(null)}
                    >
                      <Plus size={15} aria-hidden="true" />
                      新建小剧场
                    </Button>
                    {theatre.map((chat) => (
                      <ChatItem
                        key={chat.id}
                        chat={chat}
                        selected={state.target.chatId === chat.id}
                        flow={flow}
                        onDelete={setDeleting}
                        roles={state.characters}
                        client={client}
                        liveReply={state.snapshot?.chat.id === chat.id ? liveTheatreReply : null}
                        preferChinese={state.settings?.display?.preferChinesePreview}
                      />
                    ))}
                    {!theatre.length && <p className="home-empty">还没有小剧场记录</p>}
                  </>
                )}
              </TabPanel>
              {missing.length > 0 && (
                <section className="missing-chats">
                  <h2>角色缺失的对话</h2>
                  {missing.map((chat) => (
                    <ChatItem
                      key={chat.id}
                      chat={chat}
                      preferChinese={state.settings?.display?.preferChinesePreview}
                      selected={state.target.chatId === chat.id}
                      flow={flow}
                      onDelete={setDeleting}
                      roles={state.characters}
                      client={client}
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
              />
              <div className="home-sidebar-actions">
                <Button
                  variant="ghost"
                  className="home-settings"
                  aria-haspopup="dialog"
                  onClick={() => openCharacters()}
                >
                  <Users size={16} />
                  角色管理
                </Button>
                <div className="home-sidebar-action-row">
                  <Button
                    variant="ghost"
                    className="home-settings"
                    aria-haspopup="dialog"
                    onClick={() => openSettings()}
                  >
                    <Settings size={16} />
                    设置
                  </Button>
                  {downloadCount > 0 && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="home-downloads"
                      aria-label="下载中心"
                      title={'下载中心（' + downloadCount + ' 个待处理任务）'}
                      aria-haspopup="dialog"
                      aria-description={downloadCount + ' 个待处理任务'}
                      onClick={showDownloads}
                    >
                      <Download size={16} />
                      <span className="home-download-count" aria-hidden="true">
                        {downloadCount}
                      </span>
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </aside>
        </Tabs>
        <section
          className="home-stage"
          onPointerDownCapture={manualStage ? startStagePress : undefined}
          onPointerMoveCapture={manualStage ? trackStagePress : undefined}
          onPointerCancelCapture={() => {
            stagePress.current = null
          }}
          onClick={manualStage ? clickStoryStage : undefined}
          aria-label="角色舞台"
          ref={stageElement}
          tabIndex={-1}
          style={story ? undefined : stageThemeStyle}
          data-builtin-background={(!story && backgroundKnown && !currentBackground) || undefined}
          data-has-role-theme={!story && stageThemeStyle ? 'true' : undefined}
        >
          {backgroundKnown && (
            <StageBackdrop
              story={story}
              client={client}
              asset={currentBackground}
              onStartupReady={startupPending ? setDecodedBackground : undefined}
            />
          )}
          {stagePreview !== null && (
            <StageBackgroundPreview
              key={frames.at(-1)?.id ?? 'picker'}
              story={story}
              client={client}
              candidate={stagePreview}
            />
          )}
          <div
            className="home-canvas"
            ref={canvas}
            data-stage-state={stageState.loading ? 'loading' : 'ready'}
          />
          <div className={stageState.text ? 'stage-scrim active' : 'stage-scrim'} />
          {stageState.loading && !stageState.hasModel && (
            <div className="home-stage-status" role="status">
              正在加载…
            </div>
          )}
          {stageState.problem && (
            <div className="home-stage-status" role="alert">
              {stageState.problem}
              {stageState.canRetryModel && (
                <Button size="sm" variant="ghost" onClick={() => stage.current?.retryModel()}>
                  重试
                </Button>
              )}
            </div>
          )}
          {setupState.connectionProblem && (
            <div className="home-connection" role="status">
              {setupState.connectionProblem}
            </div>
          )}
          {isTheatre && !state.loading && !state.target.chatId && (
            <section className="theatre-empty-card" aria-label="小剧场引导">
              <div className="theatre-empty-icon" aria-hidden="true">
                <Theater size={24} />
              </div>
              <h2>双人小剧场</h2>
              <p>
                {theatre.length
                  ? '从左侧选择一场记录，或新建一场双人对话。'
                  : '挑选两位角色与相处情境，自动生成并演绎整场对话。'}
              </p>
              <Button onClick={() => setupTheatre(null)}>
                <Plus size={16} aria-hidden="true" />
                {theatre.length ? '创建新小剧场' : '创建第一场小剧场'}
              </Button>
            </section>
          )}
          {theatreStage?.panel === 'position' && (
            <TheatrePositions
              actors={theatreRoles}
              selected={editor.selected}
              choose={chooseSide}
              stage={stage}
              save={saveFrame}
            />
          )}
          {story && (
            <StoryRail
              expanded={railOpen}
              onExpand={toggleRail}
              onExit={exitStory}
              onLog={showHistory}
              toolsOpen={toolsOpen}
              onTools={openStageTools}
              auto={liveAuto || auto}
              canToggleAuto={!liveAuto}
              onAuto={toggleStoryAuto}
              canReplay={Boolean(replayTurnId)}
              replaying={replaying}
              onReplay={toggleStoryReplay}
              onSetup={
                theatreChat && !state.snapshot?.activeTurnId
                  ? () => setupTheatre(theatreChat)
                  : null
              }
            />
          )}
          {(!story || toolsOpen) && (
            <StageToolbar
              key={story ? 'story' : 'normal'}
              story={story}
              initialAnchor={story ? null : toolAnchor}
              onPlace={setToolAnchor}
              locked={stageLocked}
              onExpand={enterStory}
              target={state.target}
              wardrobe={wardrobe}
              background={{
                client,
                current: currentBackground,
                available:
                  !historyOpen &&
                  !state.loading &&
                  !state.sending &&
                  (!state.target.chatId || Boolean(state.snapshot)),
                open: backgroundTarget === state.target,
                close: closeBackground,
                suspended: backgroundSuspended,
                toggle: toggleBackgroundPicker,
                onPreview: setPickerPreview,
                onApply: flow.chooseBackground,
                onManage: () => showBackgrounds()
              }}
              theatre={stageTools}
              onZoom={zoomModel}
              onReset={resetModel}
            />
          )}
          <div className="home-bottom">
            {story ? (
              <StoryCaption
                subtitles={subtitles}
                state={stageState}
                manual={replayingTurn && !auto}
                advance={advanceStory}
                writable={
                  !isTheatre &&
                  Boolean(
                    state.target.characterId && !state.snapshot?.participantIssue && stageState.text
                  )
                }
                editing={editorVisible}
                toggleEditor={toggleEditor}
                draft={state.draft}
              />
            ) : (
              <Subtitle
                subtitles={subtitles}
                state={stageState}
                theatre={isTheatre}
                replayingTurn={Boolean(currentReplay && !currentReplay.error)}
                onToggleReplay={toggleStageReplay}
              />
            )}
            {story && currentReplay?.error && !isTheatre && (
              <p className="composer-problem" role="alert">
                {currentReplay.error}
              </p>
            )}
            <InteractionCards state={state} client={client} now={now} />
            {theatreChat && <ThinkingConflict flow={flow} />}
            {theatreChat && (
              <TheatreActions
                story={story}
                snapshot={state.snapshot}
                sending={state.sending}
                problem={state.problem ?? currentReplay?.error}
                confirming={replaceTarget === state.target}
                canReplay={Boolean(replayTurnId)}
                replaying={Boolean(currentReplay && !currentReplay.error)}
                canGenerate={
                  hasConnection(state) &&
                  state.snapshot.admission.canGenerate &&
                  !flow.reasoningState().conflict
                }
                modelControl={
                  <>
                    {theatreModelControl}
                    <ThinkingPicker flow={flow} />
                  </>
                }
                cancel={() => setReplaceTarget(null)}
                configure={() => setupTheatre(theatreChat)}
                replay={() => playHistoryTurn(replayTurnId)}
                stop={flow.stop}
                generate={generateTheatre}
              />
            )}
            {!isTheatre && (
              <Composer
                compact={story && !editorVisible}
                onFocus={story ? () => setWriting(true) : undefined}
                identityControl={<IdentityButton state={state} flow={flow} onOpen={openIdentity} />}
                state={state}
                flow={flow}
                asrReady={Boolean(setupState.features?.asrReady)}
                onVoiceInstall={() => setVoiceInstall(true)}
                onConfigure={() => openSettings()}
                onSend={sendMessage}
              />
            )}
          </div>
          {story && historyOpen && (
            <StoryLog
              snapshot={state.snapshot}
              characters={state.characters}
              now={now}
              onClose={hideHistory}
              play={playHistoryAudio}
              onReplayTurn={replayFromStoryLog}
            />
          )}
          {!story &&
            (historyOpen ? (
              <HistoryPanel
                showModelThinking={
                  !!state.settings?.display && state.settings.display.showModelThinking !== false
                }
                key={state.target.chatId ?? 'blank'}
                snapshot={state.snapshot}
                characters={state.characters}
                now={now}
                client={client}
                onBranch={openBranch}
                returnFromBranch={branchLink?.id === state.target.chatId ? returnFromBranch : null}
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
            ))}
        </section>
        {deleting && (
          <DeleteDialog chat={deleting} onCancel={cancelDelete} onConfirm={confirmDelete} />
        )}
        {voiceInstall && (
          <VoiceInstallDialog onCancel={() => setVoiceInstall(false)} onConfirm={goToAsr} />
        )}
      </main>
      <DialogNavigator frames={frames} dismiss={dismissDialog} renderPage={renderDialogPage} />
    </>
  )
}
