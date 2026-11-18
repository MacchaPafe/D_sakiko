import { useId, useLayoutEffect, useRef, useState } from 'react'
import { GripHorizontal, Maximize, RotateCcw, Shirt, ZoomIn, ZoomOut } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { StageWardrobe } from './StageWardrobe.jsx'
import { placeToolbar, placeWardrobe } from './stage-toolbar-layout.js'

function relativeRect(element, origin) {
  const rect = element.getBoundingClientRect()
  return {
    left: rect.left - origin.left,
    top: rect.top - origin.top,
    width: rect.width,
    height: rect.height
  }
}

function measureToolbar(bar, grip) {
  const rect = bar.getBoundingClientRect()
  const handle = relativeRect(grip, rect)
  return {
    width: rect.width,
    height: rect.height,
    gripX: handle.left + handle.width / 2,
    gripY: handle.top + handle.height / 2
  }
}

/**
 * @param {object} props 舞台视角操作和当前对话的换装入口。
 * @returns {import('react').ReactElement} 拖动中按舞台空间调整朝向的工具栏。
 */
export function StageToolbar({ target, wardrobe, onZoom, onReset }) {
  const [openFor, setOpenFor] = useState(null)
  const toolbar = useRef(null)
  const grip = useRef(null)
  const trigger = useRef(null)
  const panel = useRef(null)
  const anchor = useRef(null)
  const drag = useRef(null)
  const layout = useRef(null)
  const id = useId()
  const open = Boolean(wardrobe && openFor === target)

  useLayoutEffect(() => {
    const bar = toolbar.current
    const stage = bar.parentElement
    const currentOrientation = bar.dataset.orientation ?? 'vertical'
    bar.dataset.orientation = 'vertical'
    const vertical = measureToolbar(bar, grip.current)
    bar.dataset.orientation = 'horizontal'
    const horizontal = measureToolbar(bar, grip.current)
    bar.dataset.orientation = currentOrientation
    const sizes = { vertical, horizontal }

    function reposition() {
      const origin = stage.getBoundingClientRect()
      const stageBox = {
        left: 0,
        top: parseFloat(getComputedStyle(stage).getPropertyValue('--window-titlebar-height')) || 0,
        width: origin.width,
        height: origin.height
      }
      let barBox = relativeRect(bar, origin)
      let orientation = bar.dataset.orientation
      let size = sizes[orientation]
      let handle = { x: barBox.left + size.gripX, y: barBox.top + size.gripY, orientation }
      if (anchor.current) {
        const position = placeToolbar(anchor.current, stageBox, sizes)
        const { left, top, x, y } = position
        orientation = position.orientation
        size = sizes[orientation]
        bar.dataset.orientation = orientation
        bar.style.left = left + 'px'
        bar.style.top = top + 'px'
        bar.style.bottom = 'auto'
        barBox = { left, top, width: size.width, height: size.height }
        handle = { x, y, orientation }
        // 拖动时保留指针意图，落定后以实际手柄位置作为下一次定位的起点。
        if (drag.current) {
          anchor.current.orientation = orientation
          drag.current.position = handle
        } else anchor.current = handle
      }
      const popup = panel.current
      if (popup) {
        const box = relativeRect(popup, origin)
        const list = popup.querySelector('.stage-wardrobe-list')
        // 列表已滚动时，仍用内容原高比较可用方向，避免误以为被压缩的面板能完整显示。
        const height = Math.min(356, box.height + list.scrollHeight - list.clientHeight)
        const position = placeWardrobe(
          stageBox,
          barBox,
          relativeRect(trigger.current, origin),
          { width: box.width, height },
          orientation === 'horizontal'
        )
        popup.style.left = position.left + 'px'
        popup.style.top = position.top + 'px'
        popup.style.maxHeight = Math.min(356, position.maxHeight) + 'px'
      }
      return handle
    }

    layout.current = reposition
    reposition()
    const observer = new ResizeObserver(reposition)
    observer.observe(stage)
    if (panel.current) observer.observe(panel.current)
    return () => observer.disconnect()
  }, [open])

  useLayoutEffect(() => {
    if (!open) return
    const dialog = panel.current
    dialog.querySelector('header button')?.focus()

    function dismissOutside(event) {
      if (!dialog.contains(event.target) && !toolbar.current.contains(event.target))
        setOpenFor(null)
    }
    function dismissOnEscape(event) {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      setOpenFor(null)
      trigger.current?.focus()
    }
    document.addEventListener('pointerdown', dismissOutside)
    document.addEventListener('keydown', dismissOnEscape)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      document.removeEventListener('keydown', dismissOnEscape)
    }
  }, [open])

  function start(event) {
    if (event.button !== 0 || drag.current) return
    const handle = layout.current()
    drag.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      handle,
      moved: false
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function move(event) {
    const origin = drag.current
    if (origin?.pointerId !== event.pointerId) return
    const dx = event.clientX - origin.x
    const dy = event.clientY - origin.y
    if (!origin.moved && Math.hypot(dx, dy) < 3) return
    origin.moved = true
    anchor.current = {
      x: origin.handle.x + dx,
      y: origin.handle.y + dy,
      orientation: anchor.current?.orientation ?? origin.handle.orientation
    }
    layout.current()
  }

  function end(event) {
    const origin = drag.current
    if (origin?.pointerId !== event.pointerId) return
    drag.current = null
    if (origin.moved) anchor.current = origin.position
  }

  function toggle() {
    setOpenFor(open ? null : target)
  }

  function close() {
    setOpenFor(null)
    trigger.current?.focus()
  }

  function manage(formId) {
    close()
    wardrobe.onManage(formId)
  }

  function increase() {
    onZoom(1.1)
  }

  function decrease() {
    onZoom(1 / 1.1)
  }

  return (
    <>
      <div className="stage-toolbar" ref={toolbar} role="group" aria-label="舞台工具">
        <button
          ref={grip}
          className="toolbar-grip"
          aria-label="拖动舞台工具栏"
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={end}
          onLostPointerCapture={end}
        >
          <GripHorizontal size={16} />
        </button>
        <Button variant="ghost" size="icon" aria-label="放大角色" onClick={increase}>
          <ZoomIn />
        </Button>
        <Button variant="ghost" size="icon" aria-label="缩小角色" onClick={decrease}>
          <ZoomOut />
        </Button>
        <Button
          className="toolbar-reset"
          variant="ghost"
          size="icon"
          aria-label="重置角色视图"
          onClick={onReset}
        >
          <RotateCcw />
        </Button>
        <hr />
        <Button
          ref={trigger}
          variant="ghost"
          size="icon"
          aria-label="切换服装"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? id : undefined}
          disabled={!wardrobe}
          onClick={toggle}
        >
          <Shirt />
        </Button>
        <Button variant="ghost" size="icon" aria-label="全窗口舞台" disabled>
          <Maximize />
        </Button>
      </div>
      {wardrobe && (
        <StageWardrobe
          key={JSON.stringify([target.characterId, target.chatId])}
          {...wardrobe}
          open={open}
          panelRef={panel}
          id={id}
          onManage={manage}
          onClose={close}
        />
      )}
    </>
  )
}
