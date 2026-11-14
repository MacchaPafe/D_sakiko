import { useEffect, useState, useSyncExternalStore } from 'react'
import { createBackendClient } from '@/backend/client.js'
import { desktopHost } from '../host/desktop.js'
import { WelcomePage } from '../features/onboarding/WelcomePage.jsx'
import { SetupFlow } from '../features/setup/setup-flow.js'
import { SetupPage, SetupLoading } from '../features/setup/SetupPage.jsx'
import { CharacterPreview } from '../features/onboarding/CharacterPreview.jsx'

async function connectBackend() {
  return createBackendClient(await desktopHost().getBackendConnection())
}

/** @returns {import('react').ReactElement} 启动恢复后端阶段，当前止于资源准备，不提前完成引导或进入主页。 */
export function App() {
  const [flow] = useState(() => new SetupFlow({ connect: connectBackend }))
  const state = useSyncExternalStore(flow.subscribe, flow.getSnapshot)
  useEffect(() => {
    void flow.mount()
    return () => flow.dispose()
  }, [flow])
  if (state.view === 'loading' || state.view === 'error')
    return <SetupLoading problem={state.problem} onRetry={flow.retry} />
  const welcome = state.view === 'welcome'
  // 舞台保持在同一挂载位置，右侧换页不能销毁模型、画布或鼠标追踪。
  return (
    <main
      className={welcome ? 'welcome-page' : 'setup-page'}
      aria-label={welcome ? '欢迎使用数字小祥' : '初始化与资源准备'}
    >
      <CharacterPreview showCharacterPicker={welcome} referenceWidth={welcome ? 864 : 768} />
      {welcome ? (
        <WelcomePage
          onQuick={flow.quickStart}
          busy={Boolean(state.pending)}
          problem={state.problem}
        />
      ) : (
        <SetupPage state={state} flow={flow} />
      )}
    </main>
  )
}
