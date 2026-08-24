import { RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import neutralMascot from '../assets/access-gate/neutral.png'
import errorMascot from '../assets/access-gate/error.png'
import successMascot from '../assets/access-gate/success.png'

export function AccessGate({ state, actions }) {
  const [digits, setDigits] = useState(() => Array(6).fill(''))
  const inputRefs = useRef([])
  const submittingRef = useRef(false)
  const [visualState, setVisualState] = useState('neutral')
  const waiting = state.connection === 'checking_auth' || state.connection === 'connecting'
  const offline = state.connection === 'offline'
  const accessCode = digits.join('')
  const [retrySeconds, setRetrySeconds] = useState(0)
  const coolingDown = retrySeconds > 0

  useEffect(() => {
    const update = () => {
      setRetrySeconds(state.authRetryUntil
        ? Math.max(0, Math.ceil((state.authRetryUntil - Date.now()) / 1000))
        : 0)
    }
    update()
    if (!state.authRetryUntil) return undefined
    const timer = window.setInterval(update, 250)
    return () => window.clearInterval(timer)
  }, [state.authRetryUntil])

  const submitCode = async (code) => {
    if (code.length !== 6 || waiting || coolingDown || submittingRef.current) return
    submittingRef.current = true
    const authenticated = await actions.authenticate(code)
    submittingRef.current = false
    if (!authenticated) {
      setVisualState('error')
      setDigits(Array(6).fill(''))
      requestAnimationFrame(() => inputRefs.current[0]?.focus())
    } else {
      setVisualState('success')
    }
  }

  const updateDigit = (index, value) => {
    const digit = value.replace(/\D/g, '').slice(-1)
    setVisualState('neutral')
    const next = [...digits]
    next[index] = digit
    const code = next.join('')
    setDigits(next)
    if (digit && index < 5) inputRefs.current[index + 1]?.focus()
    if (code.length === 6) submitCode(code)
  }

  const handleKeyDown = (index, event) => {
    if (event.key === 'Backspace' && !accessCode[index] && index > 0) {
      inputRefs.current[index - 1]?.focus()
    }
  }

  const handlePaste = (event) => {
    event.preventDefault()
    const pasted = event.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    if (!pasted) return
    setVisualState('neutral')
    const next = Array(6).fill('')
    pasted.split('').forEach((digit, index) => { next[index] = digit })
    setDigits(next)
    const focusIndex = Math.min(pasted.length, 5)
    inputRefs.current[focusIndex]?.focus()
    if (pasted.length === 6) submitCode(pasted)
  }

  const submit = (event) => {
    event.preventDefault()
    submitCode(accessCode)
  }

  return (
    <main className="app-frame access-frame">
      <section className="access-screen" aria-live="polite">
        <div className={`access-mascot access-mascot--${visualState}`} aria-hidden="true">
          <img
            key={visualState}
            src={{ neutral: neutralMascot, error: errorMascot, success: successMascot }[visualState]}
            alt=""
          />
        </div>

        <div className="access-content">
          <h1>{offline ? '电脑端尚未连接' : '连接电脑端'}</h1>
          <p>{offline ? '确认电脑端服务已启动，并让手机连接同一网络。' : '输入电脑端显示的访问码'}</p>

          {offline ? (
            <button className="primary-command access-command" type="button" onClick={actions.retryConnection}>
              <RefreshCw size={18} />
              重新连接
            </button>
          ) : waiting ? (
            <div className="access-waiting">
              <span className="loading-spinner" />
              <span>{state.runtimeStatus?.message || '正在连接电脑端…'}</span>
            </div>
          ) : (
            <form className="access-form" onSubmit={submit}>
              <fieldset className="access-code-field">
                <div className={`access-code-digits ${visualState === 'error' ? 'is-error' : ''}`} role="group" aria-label="六位访问码">
                  {Array.from({ length: 6 }, (_, index) => (
                    <input
                      key={index}
                      ref={(element) => { inputRefs.current[index] = element }}
                      value={accessCode[index] || ''}
                      type="text"
                      inputMode="numeric"
                      autoComplete={index === 0 ? 'one-time-code' : 'off'}
                      maxLength={1}
                      disabled={coolingDown}
                      aria-label={`访问码第${index + 1}位`}
                      onChange={(event) => updateDigit(index, event.target.value)}
                      onKeyDown={(event) => handleKeyDown(index, event)}
                      onPaste={handlePaste}
                    />
                  ))}
                </div>
              </fieldset>
              {coolingDown && (
                <p className="access-cooldown" role="status">
                  请等待 {retrySeconds >= 60
                    ? `${Math.floor(retrySeconds / 60)} 分 ${retrySeconds % 60} 秒`
                    : `${retrySeconds} 秒`}后再试
                </p>
              )}
            </form>
          )}

          {state.error?.message && <p className="access-error" role="alert">{state.error.message}</p>}
        </div>
      </section>
    </main>
  )
}
