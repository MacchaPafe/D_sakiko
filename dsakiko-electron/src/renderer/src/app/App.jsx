import { useEffect, useState } from 'react'
import { Moon, Piano, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { runtimeClient } from '@/runtime/client'

function initialDarkMode() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/**
 * 显示空白工作区与应用启动状态，后续页面在此接入。
 * @returns {import('react').ReactElement} 应用根界面。
 */
export function App() {
  const [darkMode, setDarkMode] = useState(initialDarkMode)
  const [runtimeStatus, setRuntimeStatus] = useState('starting')

  useEffect(
    function syncTheme() {
      document.documentElement.classList.toggle('dark', darkMode)
    },
    [darkMode]
  )

  useEffect(function connectRuntime() {
    let active = true
    runtimeClient
      .getRuntimeInfo()
      .then(function handleInfo(info) {
        if (active) setRuntimeStatus(info.status)
      })
      .catch(function handleError(error) {
        console.error('无法连接桌面运行时：', error)
        if (active) setRuntimeStatus('error')
      })
    return function disconnectRuntime() {
      active = false
    }
  }, [])

  function toggleTheme() {
    setDarkMode((previous) => !previous)
  }

  let statusText = '正在启动…'
  if (runtimeStatus === 'ready') statusText = '准备就绪'
  if (runtimeStatus === 'error') statusText = '启动失败，请重新打开应用。'

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-wordmark">D_SAKIKO</span>
        <Button
          className="text-foreground"
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label={darkMode ? '切换到浅色外观' : '切换到深色外观'}
          title={darkMode ? '切换到浅色外观' : '切换到深色外观'}
        >
          {darkMode ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        </Button>
      </header>
      <main className="workspace">
        <div className="workspace-mark" aria-hidden="true">
          <Piano size={30} strokeWidth={1.25} />
        </div>
        <h1>数字小祥</h1>
        <p>空白工作区</p>
      </main>
      <footer className="app-footer">
        <span role="status" data-status={runtimeStatus}>
          {statusText}
        </span>
      </footer>
    </div>
  )
}
