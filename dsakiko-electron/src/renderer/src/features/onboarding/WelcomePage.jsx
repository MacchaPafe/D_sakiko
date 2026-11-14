import { FolderInput, LoaderCircle, Play } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import logo from '../../assets/welcome/logo.svg'
import wordmark from '../../assets/welcome/wordmark.svg'

/** @param {{onQuick: () => void, busy?: boolean, problem?: string|null}} props 快速开始操作与后端受理状态。 @returns {import('react').ReactElement} 欢迎入口，预览失败不阻止初始化。 */
export function WelcomePage({ onQuick, busy = false, problem = null }) {
  return (
    <section className="welcome-entry" aria-label="开始使用">
      <div className="welcome-card">
        <header className="welcome-brand">
          <div className="welcome-logo">
            <img src={logo} alt="" />
          </div>
          <h1>
            <img src={wordmark} alt="数字小祥" width="205" height="58" />
          </h1>
          <p>常伴桌畔的日常</p>
        </header>
        <div className="welcome-actions">
          <Button className="welcome-action" disabled={busy} onClick={onQuick}>
            {busy ? (
              <LoaderCircle className="welcome-spinner" aria-hidden="true" />
            ) : (
              <Play aria-hidden="true" />
            )}
            {busy ? '正在准备内置角色…' : '快速开始'}
          </Button>
          <Button className="welcome-action" variant="outline" disabled>
            <FolderInput aria-hidden="true" />
            从旧版本导入数据
          </Button>
          {problem && (
            <p className="setup-problem" role="alert">
              {problem}
            </p>
          )}
        </div>
      </div>
    </section>
  )
}
