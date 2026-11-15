import { useEffect, useState, useSyncExternalStore } from 'react'
import { createBackendClient } from '@/backend/client.js'
import { desktopHost } from '../host/desktop.js'
import { WelcomePage } from '../features/onboarding/WelcomePage.jsx'
import { SetupFlow } from '../features/setup/setup-flow.js'
import { SetupPage, SetupLoading } from '../features/setup/SetupPage.jsx'
import { CharacterPreview } from '../features/onboarding/CharacterPreview.jsx'
import { LegacyPage } from '../features/setup/LegacyPage.jsx'
import { HomePage } from '../features/home/HomePage.jsx'

async function connectBackend() {
  return createBackendClient(await desktopHost().getBackendConnection())
}

/** @returns {import('react').ReactElement} 按持久初始化状态恢复引导或正式主页。 */
export function App() {
  const [flow] = useState(
    () =>
      new SetupFlow({
        connect: connectBackend,
        selectLegacyDirectory: () => desktopHost().selectLegacyDirectory()
      })
  )
  const state = useSyncExternalStore(flow.subscribe, flow.getSnapshot)
  useEffect(() => {
    void flow.mount()
    return () => flow.dispose()
  }, [flow])
  if (state.view === 'loading' || state.view === 'error')
    return <SetupLoading problem={state.problem} onRetry={flow.retry} />
  if (state.view === 'home')
    return <HomePage client={flow.client} setup={flow} setupState={state} />
  const welcome = state.view === 'welcome'
  let content = <SetupPage state={state} flow={flow} />
  if (welcome)
    content = (
      <WelcomePage
        onQuick={flow.quickStart}
        onLegacy={flow.openLegacy}
        legacyDisabled={Boolean(state.onboarding?.path)}
        busy={Boolean(state.pending)}
        problem={state.problem}
      />
    )
  else if (state.view === 'legacy' || state.view === 'import')
    content = <LegacyPage state={state} flow={flow} />
  // 舞台保持在同一挂载位置，右侧换页不能销毁模型、画布或鼠标追踪。
  return (
    <main
      className={'onboarding-shell ' + (welcome ? 'welcome-page' : 'setup-page')}
      aria-label={welcome ? '欢迎使用数字小祥' : '初始化与资源准备'}
    >
      <CharacterPreview showCharacterPicker={welcome} />
      {content}
    </main>
  )
}
