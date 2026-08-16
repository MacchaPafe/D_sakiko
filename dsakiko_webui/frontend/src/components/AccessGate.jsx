import { KeyRound, RefreshCw, Server } from 'lucide-react'
import { useState } from 'react'

export function AccessGate({ state, actions }) {
  const [accessCode, setAccessCode] = useState('')
  const waiting = state.connection === 'checking_auth' || state.connection === 'connecting'
  const offline = state.connection === 'offline'

  const submit = async (event) => {
    event.preventDefault()
    if (!accessCode.trim() || waiting) return
    await actions.authenticate(accessCode.trim())
  }

  return (
    <main className="app-frame access-frame">
      <section className="access-panel" aria-live="polite">
        <div className="access-mark" aria-hidden="true">
          {offline ? <Server size={26} /> : <KeyRound size={26} />}
        </div>
        <p className="eyebrow">数字小祥 WebUI</p>
        <h1>{offline ? '电脑端尚未连接' : '连接电脑端'}</h1>

        {offline ? (
          <>
            <p className="access-copy">
              请确认电脑端服务已经启动，并让手机和电脑连接同一网络。
            </p>
            {state.error?.message && (
              <p className="access-error" role="alert">{state.error.message}</p>
            )}
            <button
              className="primary-command access-command"
              type="button"
              onClick={actions.retryConnection}
            >
              <RefreshCw size={18} />
              重新连接
            </button>
          </>
        ) : waiting ? (
          <div className="access-waiting">
            <span className="loading-spinner" />
            <span>{state.runtimeStatus?.message || '正在连接电脑端…'}</span>
          </div>
        ) : (
          <form className="access-form" onSubmit={submit}>
            <label htmlFor="access-code">电脑端显示的访问码</label>
            <input
              id="access-code"
              value={accessCode}
              type="password"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              maxLength={128}
              placeholder="输入访问码"
              onChange={(event) => setAccessCode(event.target.value)}
            />
            {state.error?.message && (
              <p className="access-error" role="alert">{state.error.message}</p>
            )}
            <button
              className="primary-command access-command"
              type="submit"
              disabled={!accessCode.trim()}
            >
              进入 WebUI
            </button>
          </form>
        )}
      </section>
    </main>
  )
}
