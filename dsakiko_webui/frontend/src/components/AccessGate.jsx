import { RefreshCw } from 'lucide-react'
import { useRef, useState } from 'react'

export function AccessGate({ state, actions }) {
  const [digits, setDigits] = useState(() => Array(6).fill(''))
  const inputRefs = useRef([])
  const submittingRef = useRef(false)
  const waiting = state.connection === 'checking_auth' || state.connection === 'connecting'
  const offline = state.connection === 'offline'
  const accessCode = digits.join('')

  const submitCode = async (code) => {
    if (code.length !== 6 || waiting || submittingRef.current) return
    submittingRef.current = true
    const authenticated = await actions.authenticate(code)
    submittingRef.current = false
    if (!authenticated) {
      setDigits(Array(6).fill(''))
      requestAnimationFrame(() => inputRefs.current[0]?.focus())
    }
  }

  const updateDigit = (index, value) => {
    const digit = value.replace(/\D/g, '').slice(-1)
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
        <header className="access-brand"><strong>数字小祥</strong></header>

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
                <div className="access-code-digits" role="group" aria-label="六位访问码">
                  {Array.from({ length: 6 }, (_, index) => (
                    <input
                      key={index}
                      ref={(element) => { inputRefs.current[index] = element }}
                      value={accessCode[index] || ''}
                      type="text"
                      inputMode="numeric"
                      autoComplete={index === 0 ? 'one-time-code' : 'off'}
                      maxLength={1}
                      aria-label={`访问码第${index + 1}位`}
                      onChange={(event) => updateDigit(index, event.target.value)}
                      onKeyDown={(event) => handleKeyDown(index, event)}
                      onPaste={handlePaste}
                    />
                  ))}
                </div>
              </fieldset>
            </form>
          )}

          {state.error?.message && <p className="access-error" role="alert">{state.error.message}</p>}
        </div>
      </section>
    </main>
  )
}
