import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  BookOpenText,
  Drama,
  LockKeyhole,
  Menu,
  Minimize,
  Play,
  Settings2,
  Square,
  X
} from 'lucide-react'
import { storyHistoryRows } from './history.js'
import { MessageText, PlayingSubtitle, SubtitleText } from './DialogueMarkdown.jsx'
import './story-stage.css'

function Glyph({ kind }) {
  const paths = {
    pen: (
      <>
        <path d="m4 20 1.3-5.5L16.7 3.1a2.3 2.3 0 0 1 3.3 3.3L8.6 17.8Z" fill="currentColor" />
        <path d="m6 13.8 4.1 4.1" stroke="#545873" strokeWidth="1.7" />
      </>
    ),
    auto: (
      <>
        <path
          d="M4.4 9a8.3 8.3 0 1 1 .4 7.2M2.5 4.9l1.2 5.4 5-1.7"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinejoin="round"
        />
        <path d="m10 8 6 4-6 4z" fill="currentColor" />
      </>
    ),
    replay: (
      <>
        <path
          d="M18.5 6A8 8 0 1 0 20 14M19 2v5h-5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
        />
        <path d="m10 8 6 4-6 4z" fill="currentColor" />
      </>
    ),
    voice: (
      <>
        <path d="M2 9h3v7H2zm5-.8L14 4v16l-7-4.1Z" fill="currentColor" />
        <path
          d="m17 7 3-2m-2 7h4m-5 5 3 2"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </>
    )
  }
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      {paths[kind]}
    </svg>
  )
}

/** @param {object} props 剧情舞台的播放状态与现有舞台操作。 @returns {import('react').ReactElement} 右侧收放工具栏。 */
export function StoryRail({
  expanded,
  onExpand,
  onExit,
  onLog,
  toolsOpen,
  onTools,
  auto,
  canToggleAuto,
  onAuto,
  theatre,
  canReplay,
  replaying,
  onReplay,
  onSetup
}) {
  const stripId = useId()
  const toggleLabel = expanded ? '收起工具栏' : '展开工具栏'
  let replayLabel = '回放'
  if (replaying) replayLabel = '停止'
  return (
    <nav className="story-rail" aria-label="剧情舞台工具栏">
      <button
        className="story-rail-toggle"
        onClick={onExpand}
        aria-controls={stripId}
        aria-expanded={expanded}
        aria-label={toggleLabel}
        title={toggleLabel}
      >
        {expanded ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
      </button>
      <div className="story-strip" id={stripId} hidden={!expanded}>
        <button onClick={onReplay} disabled={!canReplay && !replaying}>
          {replaying ? <Square /> : <Glyph kind="replay" />}
          <span>{replayLabel}</span>
        </button>
        <button
          onClick={canToggleAuto ? onAuto : undefined}
          aria-disabled={!canToggleAuto}
          aria-pressed={auto}
          className="story-auto"
          title={canToggleAuto ? '回放时自动推进台词' : '新回复自动播放，结束后恢复回放设置'}
        >
          <Glyph kind="auto" />
          {!canToggleAuto && <LockKeyhole className="story-lock" />}
          <span>自动</span>
        </button>
        <button onClick={onTools} aria-expanded={toolsOpen}>
          <Settings2 />
          <span>舞台</span>
        </button>
        {onSetup && (
          <button onClick={onSetup}>
            <Drama />
            <span>剧场设定</span>
          </button>
        )}
        <button onClick={onLog} aria-label="剧情记录">
          <BookOpenText />
          <span>记录</span>
        </button>
        <button onClick={onExit} title="放大 (Esc)">
          <Minimize />
          <span>退出</span>
        </button>
      </div>
    </nav>
  )
}

/** @param {object} props 当前一句字幕与句内翻页、写回复操作。 @returns {import('react').ReactElement} 剧情字幕及独立笔形入口。 */
export function StoryCaption({
  state,
  subtitles,
  manual,
  advance,
  writable,
  editing,
  toggleEditor,
  draft
}) {
  let reply = '回复'
  if (editing) reply = '收起回复'
  else if (draft) reply = '继续编辑'
  return (
    <div className="story-caption">
      {state.text && (
        <section aria-label="当前台词" className="story-line">
          <div className="story-speaker">{state.name}</div>
          <div className="story-words" key={state.guideId ?? state.text}>
            <PlayingSubtitle text={state.text} progress={subtitles} />
          </div>
          {state.translation && (
            <div className="story-translation">
              <SubtitleText text={state.translation} />
            </div>
          )}
          {manual && state.waiting && (
            <button className="story-next" aria-label="下一句" onClick={advance}>
              ▸
            </button>
          )}
        </section>
      )}
      <div className="story-caption-actions">
        {writable && (
          <button
            className="story-pen"
            aria-label={reply}
            title={reply}
            aria-expanded={editing}
            onClick={toggleEditor}
          >
            <Glyph kind="pen" />
          </button>
        )}
      </div>
    </div>
  )
}

/** @param {object} props 正式存档、角色目录与独立语音试听。 @returns {import('react').ReactElement} 覆盖舞台的只读剧情记录。 */
export function StoryLog({ snapshot, characters, now, onClose, play, onReplayTurn }) {
  const dialog = useRef(null)
  const list = useRef(null)
  const request = useRef(null)
  const [playing, setPlaying] = useState(null)
  const [error, setError] = useState(null)
  const rows = useMemo(
    () => storyHistoryRows(snapshot, now, characters),
    [snapshot, now, characters]
  )
  useLayoutEffect(() => {
    dialog.current.showModal()
    list.current.scrollTop = list.current.scrollHeight
  }, [])
  useEffect(() => () => request.current?.abort(), [])
  function listen(row) {
    const repeat = playing === row.id
    request.current?.abort()
    setError(null)
    if (repeat) return
    const active = new AbortController()
    request.current = active
    setPlaying(row.id)
    play(row.audio, active.signal, (problem) => {
      if (request.current !== active) return
      request.current = null
      setPlaying(null)
      if (problem) setError({ id: row.id, text: problem.message })
    })
  }
  return (
    <dialog ref={dialog} className="story-log" aria-label="剧情记录">
      <button className="story-log-close" aria-label="关闭剧情记录" onClick={onClose} autoFocus>
        <X />
      </button>
      <div className="story-log-list" ref={list} tabIndex={0}>
        {rows.length === 0 && <p className="story-log-empty">暂无台词</p>}
        {rows.map((row) => (
          <article key={row.id} className="story-entry" data-kind={row.kind}>
            <div className="story-speaker">{row.name}</div>
            <MessageText text={row.text} />
            {row.attachments?.map((file) => (
              <small key={file.id ?? file.name}>{file.name ?? file.filename}</small>
            ))}
            {row.translation && <p className="story-translation">{row.translation}</p>}
            {row.kind === 'user' && row.replayTurnId && (
              <button
                className="story-entry-action"
                aria-label="回放本轮回复"
                title="回放本轮回复"
                onClick={() => onReplayTurn(row.replayTurnId)}
              >
                <Play size={18} fill="currentColor" aria-hidden="true" />
              </button>
            )}
            {row.audio && (
              <button
                className="story-entry-action"
                aria-label={playing === row.id ? '停止试听' : '重播语音'}
                onClick={() => listen(row)}
              >
                {playing === row.id ? <Square /> : <Glyph kind="voice" />}
              </button>
            )}
            {error?.id === row.id && <p role="alert">{error.text}</p>}
          </article>
        ))}
      </div>
    </dialog>
  )
}
