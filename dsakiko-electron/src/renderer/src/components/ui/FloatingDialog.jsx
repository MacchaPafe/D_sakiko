import { useLayoutEffect, useRef } from 'react'
import './floating-dialog.css'

/**
 * @param {{label: string, className?: string, onClose: () => void, children: import('react').ReactNode}} props 对话框名称、内容与关闭回调。
 * @returns {import('react').ReactElement} 与设置同样的浮层外壳，原生对话框管理焦点和层级。
 */
export function FloatingDialog({ label, className = '', onClose, children }) {
  const element = useRef(null)
  useLayoutEffect(() => {
    const dialog = element.current
    dialog.showModal()
    return () => dialog.close()
  }, [])

  function cancel(event) {
    // 文件选择器取消时，input 的 cancel 事件也会冒泡到外层浮窗。
    if (event.target !== event.currentTarget) return
    event.preventDefault()
    onClose()
  }

  return (
    <dialog
      className={'floating-dialog ' + className}
      aria-label={label}
      ref={element}
      onCancel={cancel}
    >
      {children}
    </dialog>
  )
}
