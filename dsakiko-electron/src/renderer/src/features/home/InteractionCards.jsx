import { useEffect, useRef, useState } from 'react'
import { Button } from '../../components/ui/button.jsx'

function Question({ item, client }) {
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const choices = useRef([])
  async function submit(value) {
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
    try {
      await client.interactions.complete({ requestId: item.requestId, status: 'succeeded', value })
    } catch (failure) {
      setError(failure.message)
      submitting.current = false
      setBusy(false)
    }
  }
  useEffect(() => {
    function shortcuts(event) {
      if (
        event.repeat ||
        event.isComposing ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        document.querySelector('dialog[open],[aria-modal="true"], :popover-open, [role="menu"]') ||
        event.target.closest?.('input,textarea,select,[contenteditable="true"]')
      )
        return
      const current = choices.current.indexOf(document.activeElement)
      if (current >= 0 && ['ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault()
        choices.current[
          (current + choices.current.length + (event.key === 'ArrowDown' ? 1 : -1)) %
            choices.current.length
        ]?.focus()
      }
      if (!/^[1-9]$/.test(event.key)) return
      const button = choices.current[Number(event.key) - 1]
      if (!button) return
      event.preventDefault()
      if (document.activeElement === button) button.click()
      else button.focus()
    }
    window.addEventListener('keydown', shortcuts)
    return () => window.removeEventListener('keydown', shortcuts)
  }, [])
  function send(event) {
    event.preventDefault()
    if (answer.trim()) void submit({ kind: 'answered', answer: answer.trim() })
  }
  return (
    <>
      <strong>{item.payload.question}</strong>
      <div className="interaction-options">
        {(item.payload.choices ?? []).map((text, i) => (
          <button
            key={i}
            type="button"
            ref={(el) => {
              choices.current[i] = el
            }}
            disabled={busy}
            onClick={() => submit({ kind: 'answered', answer: text })}
          >
            <kbd>{i + 1}</kbd>
            {text}
          </button>
        ))}
      </div>
      <form onSubmit={send}>
        <input
          placeholder="输入你想到的回复"
          aria-label="输入你想到的回复"
          maxLength={4000}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
        <Button size="sm" disabled={busy || !answer.trim()}>
          回答
        </Button>
      </form>
      <footer>
        <button type="button" disabled={busy} onClick={() => submit({ kind: 'skipped' })}>
          跳过
        </button>
      </footer>
      {error && <p role="alert">{error}</p>}
    </>
  )
}

function Lottery({ item, chatId, client, now }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [frameTime, setFrameTime] = useState(Date.now)
  const list = useRef(null)
  const drawn = Date.parse(item.drawnAt)
  const remaining = item.revealAt - Math.max(now, frameTime, drawn)
  const animating = item.result && remaining > 0
  const shown = item.result && !animating
  let selected = -1
  if (item.result) {
    const progress = animating
      ? Math.max(0, Math.min(1, 1 - remaining / (item.revealAt - drawn)))
      : 1
    const eased = progress * progress * (3 - 2 * progress)
    selected =
      Math.floor(eased * (item.options.length * 2 + item.result.index)) % item.options.length
  }
  useEffect(() => {
    if (!animating) return
    const timer = setInterval(() => setFrameTime(Date.now()), 32)
    return () => clearInterval(timer)
  }, [animating])
  useEffect(() => {
    if (selected >= 0 && list.current) {
      const el = list.current.children[selected]
      if (el)
        list.current.scrollTop = Math.max(
          0,
          el.offsetTop - list.current.offsetTop - list.current.clientHeight / 2
        )
    }
  }, [selected])
  async function draw() {
    setBusy(true)
    try {
      await client.conversations.drawLottery(
        chatId,
        item.id,
        matchMedia('(prefers-reduced-motion: reduce)').matches
      )
    } catch (failure) {
      setError(failure.message)
      setBusy(false)
    }
  }
  return (
    <>
      <strong>{item.title}</strong>
      <div className="interaction-options lottery-options" ref={list}>
        {item.options.map((option, i) => (
          <div key={i} data-current={i === selected}>
            {option}
          </div>
        ))}
      </div>
      <footer>
        {shown ? (
          <strong>抽中：{item.result.option}</strong>
        ) : (
          <Button size="sm" disabled={busy || Boolean(item.result)} onClick={draw}>
            {animating ? '正在抽签' : '开始抽签'}
          </Button>
        )}
      </footer>
      {error && <p role="alert">{error}</p>}
    </>
  )
}

/** @param {object} props 互动请求、当前对话持久抽签及后端接口。 @returns {import('react').ReactElement|null} 唯一处于前台的互动卡。 */
export function InteractionCards({ state, client, now }) {
  const chat = state.snapshot?.chat
  if (!chat) return null
  const order = new Map()
  const sourceKey = (source) => JSON.stringify([source.turnId, source.callId])
  for (const turn of chat.turns)
    for (const message of turn.messages)
      for (const part of message.parts ?? [])
        if (part.kind === 'tool-call')
          order.set(sourceKey({ turnId: turn.id, callId: part.callId }), order.size)
  const lotteries = (chat.meta.lotteries ?? []).filter(
    (item) => !item.cancelled && order.has(sourceKey(item.source))
  )
  const pending = [
    ...(state.questions ?? []).filter(
      (q) => q.chatId === chat.id && order.has(sourceKey(q.source))
    ),
    ...lotteries.filter((l) => !l.result || l.revealAt > now)
  ].sort((a, b) => order.get(sourceKey(a.source)) - order.get(sourceKey(b.source)))
  const last = lotteries.at(-1)
  const item =
    pending[0] ?? (last && order.get(sourceKey(last.source)) === order.size - 1 ? last : null)
  if (!item) return null
  return (
    <section
      className="interaction-card"
      aria-label={item.kind === 'question' ? '角色提问' : '抽签'}
      key={item.requestId ?? item.id}
    >
      {pending.length > 1 && <small>1 / {pending.length}</small>}
      {item.kind === 'question' ? (
        <Question item={item} client={client} />
      ) : (
        <Lottery item={item} client={client} chatId={chat.id} now={now} />
      )}
    </section>
  )
}
