import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ChevronRight, Play, Square, Volume2 } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'

import { historyRows } from './history.js'
import { fitHistorySize, HISTORY_SIZE_KEY, readHistorySize } from './history-size.js'

function inline(text) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={index}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={index}>{part.slice(1, -1)}</code>
    return part
  })
}

function MarkdownLine({ line }) {
  if (/^\s*[-*+] /.test(line))
    return <div className="message-list-item">• {inline(line.replace(/^\s*[-*+] /, ''))}</div>
  if (/^\s*\d+\. /.test(line)) return <div className="message-list-item">{inline(line)}</div>
  if (/^#{1,3} /.test(line)) return <strong>{inline(line.replace(/^#{1,3} /, ''))}</strong>
  if (line.startsWith('> ')) return <blockquote>{inline(line.slice(2))}</blockquote>
  return <div>{line ? inline(line) : <br />}</div>
}
function MarkdownBlock({ block, code }) {
  if (code)
    return (
      <pre>
        <code>{block}</code>
      </pre>
    )
  return block.split('\n').map((line, index) => <MarkdownLine key={index} line={line} />)
}
/** @param {{text: string}} props 消息文本。 @returns {import('react').ReactElement} 基础 Markdown；不执行 HTML 或加载外部媒体。 */
export function MessageText({ text }) {
  const blocks = String(text ?? '').split(/```[^\n]*\n([\s\S]*?)```/g)
  return (
    <div className="message-text">
      {blocks.map((block, index) => (
        <MarkdownBlock key={index} block={block} code={index % 2 === 1} />
      ))}
    </div>
  )
}

function toolStatus(tool) {
  return (
    {
      succeeded: '已完成',
      failed: '失败',
      interrupted: '已中断',
      accepted: '等待结果',
      disabled: '已禁用'
    }[tool.outcome?.status] ?? '调用中'
  )
}
function Sources({ items }) {
  const sources = new Map()
  for (const source of items ?? []) {
    try {
      const url = new URL(source.url)
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
        sources.set(url.href, source.title || url.hostname)
    } catch {
      /* 旧存档可能包含无效地址。 */
    }
  }
  function open(event) {
    if (!window.dsakikoHost?.openWebSource) return
    event.preventDefault()
    void window.dsakikoHost.openWebSource(event.currentTarget.href)
  }
  return (
    <ul className="web-sources">
      {[...sources].map(([url, title]) => (
        <li key={url}>
          <a href={url} title={url} target="_blank" rel="noreferrer" onClick={open}>
            {title}
          </a>
        </li>
      ))}
    </ul>
  )
}

function FileDownloads({ client, files }) {
  const [error, setError] = useState('')
  async function download(file) {
    let access
    try {
      setError('')
      access = await client.assets.access(file.asset)
      const response = await fetch(access.url)
      if (!response.ok) throw new Error('无法读取文件。')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = file.name
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure) {
      setError(failure.message)
    } finally {
      if (access) void client.assets.releaseAccess(access.leaseId).catch(() => {})
    }
  }
  return (
    <div className="history-files">
      {files.map((file) => (
        <button
          type="button"
          key={file.asset.id + file.name}
          title={'下载 ' + file.name}
          onClick={() => download(file)}
        >
          {file.name}
        </button>
      ))}
      {error && <span role="alert">{error}</span>}
    </div>
  )
}
function ToolDetails({ tool }) {
  const value = tool.display ?? tool.outcome?.value ?? tool.outcome?.problem ?? tool.outcome
  const result = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return (
    <section className="history-tool-detail">
      <strong>{tool.toolName}</strong>
      <span>{toolStatus(tool)}</span>
      <div>参数</div>
      <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
      <div>结果</div>
      <pre>{result ?? '等待工具返回结果'}</pre>
      {tool.sources?.length > 0 && <Sources items={tool.sources} />}
    </section>
  )
}
function HistoryMessage({
  client,
  theatre,
  row,
  characters,
  playback,
  turnReplay,
  onPlay,
  onReplayTurn
}) {
  if (row.kind === 'sources')
    return (
      <details className="history-tools">
        <summary>引用来源</summary>
        <Sources items={row.sources} />
      </details>
    )
  if (row.kind === 'tools') {
    const pending = row.tools.some((tool) => !tool.outcome || tool.outcome.status === 'accepted')
    const failed = row.tools.some((tool) =>
      ['failed', 'interrupted'].includes(tool.outcome?.status)
    )
    let status = ''
    if (pending) status = ' · 进行中'
    else if (failed) status = ' · 有未完成项'
    return (
      <details className="history-tools">
        <summary>调用了工具{status}</summary>
        {row.tools.map((tool) => (
          <ToolDetails key={tool.id} tool={tool} />
        ))}
      </details>
    )
  }
  const role = characters.find((item) => item.id === row.name)
  const audio = row.kind === 'assistant' ? row.audio : null
  const linePlayback = playback?.id === row.id ? playback : null
  const turnPlayback = turnReplay?.id === row.replayTurnId ? turnReplay : null
  const playing = linePlayback && !linePlayback.error
  const replaying = turnPlayback && !turnPlayback.error
  const playbackLabel = playing ? '停止语音' : '播放语音'
  let replayLabel = theatre ? '回放整场' : '回放本轮回复'
  if (replaying) replayLabel = '停止回放'
  function toggleAudio() {
    onPlay(row.id, audio)
  }
  function toggleTurn() {
    onReplayTurn(row.replayTurnId)
  }
  return (
    <article className={'history-message history-' + row.kind}>
      <div className="history-message-header">
        <span className="history-speaker">{role?.displayName ?? role?.name ?? row.name}</span>
        {audio && (
          <Button
            variant="ghost"
            size="icon-xs"
            className="history-play-audio"
            aria-label={playbackLabel}
            title={playbackLabel}
            onClick={toggleAudio}
          >
            {playing ? <Square /> : <Volume2 />}
          </Button>
        )}
      </div>
      <div
        className={theatre ? 'history-message-body history-theatre-line' : 'history-message-body'}
      >
        {row.replayTurnId && (
          <Button
            variant="ghost"
            size="icon-sm"
            className="history-play-audio"
            aria-label={replayLabel}
            title={replayLabel}
            onClick={toggleTurn}
          >
            {replaying ? <Square /> : <Play />}
          </Button>
        )}
        <div className="history-bubble">
          <MessageText text={row.text} />
          {row.attachments && <FileDownloads client={client} files={row.attachments} />}
          {row.kind === 'assistant' && row.translation && (
            <div className="history-translation">
              <MessageText text={row.translation} />
            </div>
          )}
        </div>
      </div>
      {(linePlayback?.error || turnPlayback?.error) && (
        <p className="home-inline-problem" role="status">
          {turnPlayback?.error ?? '这句语音无法播放'}
        </p>
      )}
    </article>
  )
}

/**
 * @param {{snapshot: object|null, characters: object[], onClose: () => void, onPlayAudio: (audio: import('./history.js').AudioMaterial, signal: AbortSignal, onStop: (error?: Error) => void) => void, onPlayTurn: (turnId: string) => void, turnReplay: {id: string, error: string|null}|null}} props 对话观察、单句试听与由舞台管理的整轮回放。
 * @returns {import('react').ReactElement} 独立滚动的悬浮历史，离开底部时不抢占阅读位置。
 */
export function HistoryPanel({
  now,
  client,
  snapshot,
  characters,
  onClose,
  onPlayAudio,
  onPlayTurn,
  turnReplay
}) {
  const panel = useRef(null)
  const scroll = useRef(null)
  const following = useRef(true)
  const [unread, setUnread] = useState(false)
  const [preferred, setPreferred] = useState(readHistorySize)
  const [available, setAvailable] = useState({ width: 360, height: 671 })
  const [playback, setPlayback] = useState(null)
  const playbackRequest = useRef(null)
  const drag = useRef(null)
  const currentSize = useRef(preferred)
  const size = fitHistorySize(preferred, available)
  useEffect(() => () => playbackRequest.current?.abort(), [])

  function playAudio(id, audio) {
    playbackRequest.current?.abort()
    if (playback?.id === id && !playback.error) {
      playbackRequest.current = null
      setPlayback(null)
      return
    }
    const request = new AbortController()
    playbackRequest.current = request
    setPlayback({ id, error: null })
    onPlayAudio(audio, request.signal, (error) => {
      if (request.signal.aborted || playbackRequest.current !== request) return
      playbackRequest.current = null
      setPlayback(error ? { id, error: true } : null)
    })
  }
  function replayTurn(turnId) {
    playbackRequest.current?.abort()
    playbackRequest.current = null
    setPlayback(null)
    onPlayTurn(turnId)
  }
  useEffect(() => {
    const element = panel.current
    function measure() {
      const style = getComputedStyle(element)
      setAvailable({
        width: element.parentElement.clientWidth - parseFloat(style.right) - 24,
        height: element.parentElement.clientHeight - parseFloat(style.top) - 182
      })
    }
    const observer = new ResizeObserver(measure)
    observer.observe(element.parentElement)
    measure()
    return () => observer.disconnect()
  }, [])
  const rows = historyRows(snapshot, now)
  const warnings = snapshot?.chat.meta.extensions?.pythonImport?.normalizationWarnings ?? []
  // 语音和回放按钮可以在轮次结束后出现，记录内容没有变化时不提示新消息。
  const contentKey = JSON.stringify(
    rows.map((row) => ({
      ...row,
      replayTurnId: null,
      ...(row.kind === 'assistant' ? { audio: null } : {})
    }))
  )
  useEffect(() => {
    if (following.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
    else setUnread(true)
  }, [contentKey, snapshot?.chat.id])
  function saveSize(value) {
    currentSize.current = value
    setPreferred(value)
  }
  function beginResize(event) {
    if (event.button !== 0) return
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      ...size,
      axis: event.currentTarget.dataset.axis
    }
    currentSize.current = size
    event.currentTarget.setPointerCapture(event.pointerId)
    event.preventDefault()
  }
  function resize(event) {
    const origin = drag.current
    if (!origin) return
    let width = origin.width
    let height = origin.height
    if (origin.axis !== 'height') width += origin.x - event.clientX
    if (origin.axis !== 'width') height += event.clientY - origin.y
    applyResize(width, height, origin.axis)
  }
  function applyResize(width, height, axis) {
    const fitted = fitHistorySize(
      { width: Math.max(280, width), height: Math.max(220, height) },
      available
    )
    // 只拖动宽度时，不能把小窗口限制下的实际高度写回用户的高度偏好。
    saveSize({
      width: axis === 'height' ? preferred.width : fitted.width,
      height: axis === 'width' ? preferred.height : fitted.height
    })
  }
  function rememberSize() {
    try {
      localStorage.setItem(HISTORY_SIZE_KEY, JSON.stringify(currentSize.current))
    } catch {
      /* 本地存储不可用时仍允许调整。 */
    }
  }
  function finishResize() {
    if (!drag.current) return
    drag.current = null
    rememberSize()
  }
  function resizeWithKeyboard(event) {
    const axis = event.currentTarget.dataset.axis
    let { width, height } = size
    if (axis !== 'height' && event.key === 'ArrowLeft') width += 20
    else if (axis !== 'height' && event.key === 'ArrowRight') width -= 20
    else if (axis !== 'width' && event.key === 'ArrowDown') height += 20
    else if (axis !== 'width' && event.key === 'ArrowUp') height -= 20
    else return
    event.preventDefault()
    const changedAxis = ['ArrowLeft', 'ArrowRight'].includes(event.key) ? 'width' : 'height'
    applyResize(width, height, changedAxis)
    rememberSize()
  }
  function handleScroll() {
    const element = scroll.current
    following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 36
    if (following.current) setUnread(false)
  }
  function latest() {
    following.current = true
    scroll.current.scrollTop = scroll.current.scrollHeight
    setUnread(false)
  }
  const theatre = snapshot?.chat.mode === 'scripted-dialogue'
  return (
    <aside className="history-panel" aria-label="聊天记录" ref={panel} style={size}>
      <header>
        <span>{theatre ? '小剧场记录' : '聊天记录'}</span>
        <Button variant="ghost" size="icon" aria-label="收起聊天记录" onClick={onClose}>
          <ChevronRight />
        </Button>
      </header>
      <div className="history-scroll" ref={scroll} onScroll={handleScroll}>
        {!snapshot && <p className="home-empty">开始一段新的对话吧。</p>}
        {snapshot?.participantIssue && (
          <p className="home-inline-problem">
            {snapshot.participantIssue.message ?? '角色缺失，此对话仅供阅读。'}
          </p>
        )}
        {warnings.some((warning) => warning.code === 'legacy_chat_model_unavailable') && (
          <p className="home-inline-problem">原对话的部分模型文件已缺失，当前使用默认模型。</p>
        )}
        {warnings.some((warning) => warning.code === 'legacy_tool_order_unresolved') && (
          <p className="home-inline-problem">部分旧工具调用的位置无法确认，已保留其原有顺序。</p>
        )}
        {snapshot?.chat.meta.workspaceFiles?.length > 0 && (
          <details className="history-tools">
            <summary>会话文件</summary>
            <FileDownloads client={client} files={snapshot.chat.meta.workspaceFiles} />
          </details>
        )}
        {rows.map((row) => (
          <HistoryMessage
            client={client}
            theatre={snapshot?.chat.mode === 'scripted-dialogue'}
            key={row.id}
            row={row}
            characters={characters}
            playback={playback}
            turnReplay={turnReplay}
            onPlay={playAudio}
            onReplayTurn={replayTurn}
          />
        ))}
        {snapshot?.problem && <p className="home-inline-problem">{snapshot.problem.message}</p>}
      </div>
      {unread && (
        <Button className="history-latest" size="sm" onClick={latest}>
          <ArrowDown />
          新消息
        </Button>
      )}
      {['width', 'height', 'both'].map((axis) => (
        <button
          key={axis}
          className={'history-resize history-resize-' + axis}
          data-axis={axis}
          aria-label={
            { width: '调整记录框宽度', height: '调整记录框高度', both: '调整记录框大小' }[axis]
          }
          onPointerDown={beginResize}
          onPointerMove={resize}
          onPointerUp={finishResize}
          onLostPointerCapture={finishResize}
          onKeyDown={resizeWithKeyboard}
        />
      ))}
    </aside>
  )
}
