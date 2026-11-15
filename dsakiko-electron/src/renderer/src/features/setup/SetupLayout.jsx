import { Check } from 'lucide-react'
import logo from '../../assets/welcome/logo.svg'
import wordmark from '../../assets/welcome/wordmark.svg'

/** @param {{firstStage: string, activeStage?: number, view: string, children: import('react').ReactNode, footer: import('react').ReactNode, scrollLabel?: string}} props 两阶段引导的公共布局及当前子页。 @returns {import('react').ReactElement} 品牌、只读阶段、滚动内容和固定底部；舞台由 App 持有。 */
export function SetupLayout({
  firstStage,
  activeStage = 2,
  view,
  children,
  footer,
  scrollLabel = '资源详情'
}) {
  return (
    <section className="setup-panel">
      <header className="setup-brand">
        <img className="setup-logo" src={logo} alt="" />
        <img src={wordmark} alt="数字小祥" width="132" height="38" />
      </header>
      <ol className="setup-steps" aria-label="初始化阶段">
        <li aria-current={activeStage === 1 ? 'step' : undefined}>
          <span>
            {activeStage === 2 ? (
              <Check size={16} aria-hidden="true" />
            ) : (
              <span className="setup-step-number">1</span>
            )}
            {firstStage}
          </span>
          <div />
        </li>
        <li aria-current={activeStage === 2 ? 'step' : undefined}>
          <span>
            <span className="setup-step-number">2</span>添加语音（可选）
          </span>
          <div />
        </li>
      </ol>
      <div key={view} className="setup-scroll" tabIndex={0} aria-label={scrollLabel}>
        <div className="setup-view-content">{children}</div>
      </div>
      {footer}
    </section>
  )
}
