import { useCallback, useRef, useState } from 'react'

/** @typedef {{title: string, layout?: string}} DialogPage 每个浮窗视图都有名称，首页可以指定窗口布局。 */
/** @template {DialogPage} T @typedef {{id: number, page: T}} DialogFrame 同类型的视图可以多次进入，各自保留状态。 */

/**
 * @template {DialogPage} T
 * @returns {{frames: Array<DialogFrame<T>>, open: (page: T) => void, push: (page: T) => void, replace: (id: number, page: T) => void, dismiss: (id: number) => void}} 浮窗的替换、前进和返回操作。
 */
export function useDialogNavigation() {
  const [frames, setFrames] = useState([])
  const nextId = useRef(0)
  const open = useCallback((page) => {
    const frame = { id: ++nextId.current, page }
    setFrames([frame])
  }, [])
  const push = useCallback((page) => {
    const frame = { id: ++nextId.current, page }
    setFrames((current) => [...current, frame])
  }, [])
  const replace = useCallback((id, page) => {
    const frame = { id: ++nextId.current, page }
    setFrames((current) => {
      // 异步提交可能晚于用户的导航，只有发起提交的视图还在前台才可以跳转。
      return current.at(-1)?.id === id ? [...current.slice(0, -1), frame] : current
    })
  }, [])
  const dismiss = useCallback((id) => {
    setFrames((current) => (current.at(-1)?.id === id ? current.slice(0, -1) : current))
  }, [])
  return { frames, open, push, replace, dismiss }
}
