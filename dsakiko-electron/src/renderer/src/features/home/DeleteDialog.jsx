import { useEffect, useRef, useState } from 'react'
import { Button } from '../../components/ui/button.jsx'

/** @param {{chat: object, onCancel: () => void, onConfirm: () => Promise<boolean>}} props 待删除的正式聊天。 @returns {import('react').ReactElement} 原生模态框负责焦点限制与 Escape；失败保留确认界面。 */
export function DeleteDialog({ chat, onCancel, onConfirm }) {
  const element = useRef(null)
  const [pending, setPending] = useState(false)
  useEffect(() => {
    element.current.showModal()
  }, [])
  async function confirm() {
    if (pending) return
    setPending(true)
    const done = await onConfirm()
    if (!done) setPending(false)
  }
  function cancel() {
    if (pending) return
    // 先关闭原生弹窗，让焦点回到侧栏的删除入口，再由主页卸载弹窗。
    element.current.close()
    onCancel()
  }
  function handleEscape(event) {
    event.preventDefault()
    cancel()
  }
  return (
    <dialog
      ref={element}
      className="home-delete"
      aria-labelledby="delete-title"
      onCancel={handleEscape}
    >
      <h2 id="delete-title">删除这段对话？</h2>
      <p>{chat.title || '未命名对话'}</p>
      <div>
        <Button autoFocus variant="outline" disabled={pending} onClick={cancel}>
          取消
        </Button>
        <Button disabled={pending} onClick={confirm}>
          {pending ? '正在删除…' : '删除'}
        </Button>
      </div>
    </dialog>
  )
}
