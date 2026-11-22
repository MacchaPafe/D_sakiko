import { useLayoutEffect, useRef } from 'react'
import { FloatingDialog } from './FloatingDialog.jsx'

/** @typedef {{onDismiss: () => void, returnTo: string|null, id: number}} DialogPageControls 当前视图的关闭或返回动作。 */

/**
 * 视图可用 data-dialog-focus 指定初始焦点，用 data-dialog-busy 暂停 Escape 返回。
 * 需要在返回前提交编辑的视图可用 data-dialog-escape 接管 Escape。
 * @template {import('./use-dialog-navigation.js').DialogPage} T
 * @param {{frames: Array<import('./use-dialog-navigation.js').DialogFrame<T>>, dismiss: (id: number) => void, renderPage: (page: T, controls: DialogPageControls) => import('react').ReactNode}} props 通用导航状态和业务视图渲染入口。
 * @returns {import('react').ReactElement|null} 一个原生浮窗，保留前一视图的表单、滚动和焦点。
 */
export function DialogNavigator({ frames, dismiss, renderPage }) {
  const pages = useRef(new Map())
  const previousFocus = useRef(new WeakMap())
  const active = frames.at(-1)
  const activeId = active?.id

  useLayoutEffect(() => {
    const pane = pages.current.get(activeId)
    if (!pane) return
    const remembered = previousFocus.current.get(pane)
    const target =
      remembered && pane.contains(remembered) && !remembered.matches(':disabled')
        ? remembered
        : (pane.querySelector('[data-dialog-focus]') ?? pane)
    target.focus({ preventScroll: true })
    if (!pane.contains(document.activeElement)) pane.focus({ preventScroll: true })
  }, [activeId])

  if (!active) return null

  function cancel() {
    const pane = pages.current.get(active.id)
    if (pane?.querySelector('[data-dialog-busy]')) return
    const receiver = pane?.querySelector('[data-dialog-escape]')
    if (receiver && !receiver.dispatchEvent(new Event('dialog-escape', { cancelable: true }))) return
    dismiss(active.id)
  }
  function renderFrame(frame, index) {
    const hidden = frame.id !== active.id
    function rememberFocus(event) {
      previousFocus.current.set(event.currentTarget, event.target)
    }
    function attachPane(element) {
      if (element) pages.current.set(frame.id, element)
      else pages.current.delete(frame.id)
    }
    return (
      <div
        key={frame.id}
        ref={attachPane}
        className="floating-dialog-page"
        hidden={hidden}
        inert={hidden}
        tabIndex={-1}
        onFocusCapture={rememberFocus}
      >
        {renderPage(frame.page, {
          id: frame.id,
          onDismiss: () => dismiss(frame.id),
          returnTo: frames[index - 1]?.page.title ?? null
        })}
      </div>
    )
  }

  const layout = active.page.layout ?? frames[0].page.layout

  return (
    <FloatingDialog className={layout} label={active.page.title} onClose={cancel}>
      {frames.map(renderFrame)}
    </FloatingDialog>
  )
}
