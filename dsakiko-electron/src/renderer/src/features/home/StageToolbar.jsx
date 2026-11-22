import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { BackgroundPicker } from './BackgroundGallery.jsx'
import {
  Image as Picture,
  GripHorizontal,
  Move,
  X,
  Maximize,
  RotateCcw,
  Shirt,
  ZoomIn,
  ZoomOut
} from 'lucide-react'
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
 * 舞台工具栏与剧场舞台调整／换装浮窗。
 * @param {object} props 组件属性。
 * @param {{characterId: string|null, chatId: string|null, tab: string}} props.target 当前激活的对话目标。
 * @param {boolean} [props.story] 是否使用剧情舞台的固定工具位置。
 * @param {boolean} [props.locked] 回放期间锁定换装和背景。
 * @param {() => void} [props.onExpand] 打开全窗口舞台。
 * @param {object} [props.initialAnchor] 普通舞台记住的拖动手柄位置。
 * @param {object|null} props.wardrobe 换装浮窗配置，不可用时为 null。
 * @param {(factor: number) => void} props.onZoom 舞台角色缩放回调。
 * @param {() => void} props.onReset 舞台视角或选中演员位置重置回调。
 * @param {{actors: Array<{id: string, displayName: string}>, panel: 'position'|'orientation'|null, selected: number|null|undefined, setPanel: (panel: 'position'|'orientation'|null) => void, face: (orientation: 'screen'|'face-to-face') => void, canFace: boolean, inward: boolean, degradedNote: string}|null} props.theatre 小剧场舞台调整配置。
 * @returns {import('react').ReactElement} 拖动中按舞台空间调整朝向的工具栏。
 */
export function StageToolbar({
  target,
  wardrobe,
  background,
  onZoom,
  onReset,
  theatre,
  story = false,
  locked = false,
  onExpand,
  initialAnchor,
  onPlace
}) {
  const [openFor, setOpenFor] = useState(null)
  const backgroundTrigger = useRef(null)
  const adjust = useRef(null)
  const toolbar = useRef(null)
  const grip = useRef(null)
  const trigger = useRef(null)
  const panel = useRef(null)
  const anchor = useRef(initialAnchor ?? null)
  const drag = useRef(null)
  const layout = useRef(null)
  const id = useId()
  const backgroundOpen = background.open
  const closeBackground = background.close
  const suspended = background.suspended
  const open = Boolean(!backgroundOpen && wardrobe && openFor === target)

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
      if (anchor.current && !story) {
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
        const height = Math.min(
          356,
          box.height + (list ? list.scrollHeight - list.clientHeight : 0)
        )
        const position = placeWardrobe(
          stageBox,
          barBox,
          relativeRect(
            (theatre?.panel && adjust.current) ||
              (backgroundOpen && backgroundTrigger.current) ||
              trigger.current,
            origin
          ),
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
  }, [
    open,
    backgroundOpen,
    theatre?.panel,
    theatre?.selected,
    theatre?.canFace,
    theatre?.degradedNote,
    story,
    anchor
  ])

  useLayoutEffect(() => {
    if ((!open && !backgroundOpen) || suspended) return
    const dialog = panel.current
    if (!dialog.contains(document.activeElement))
      (
        dialog.querySelector('input[type="search"]') ?? dialog.querySelector('header button')
      )?.focus()

    function dismissOutside(event) {
      if (!dialog.contains(event.target) && !toolbar.current.contains(event.target)) {
        setOpenFor(null)
        closeBackground()
      }
    }
    function dismissOnEscape(event) {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      setOpenFor(null)
      closeBackground()
      const button = backgroundOpen ? backgroundTrigger : trigger
      button.current?.focus()
    }
    document.addEventListener('pointerdown', dismissOutside)
    document.addEventListener('keydown', dismissOnEscape)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      document.removeEventListener('keydown', dismissOnEscape)
    }
  }, [open, backgroundOpen, suspended, closeBackground])

  const activeTheatrePanel = Boolean(theatre?.panel)
  const closeTheatrePanel = theatre?.setPanel
  useEffect(() => {
    if (!activeTheatrePanel) return
    function dismissPanelOnEscape(event) {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      closeTheatrePanel?.(null)
      adjust.current?.focus()
    }
    document.addEventListener('keydown', dismissPanelOnEscape)
    return () => document.removeEventListener('keydown', dismissPanelOnEscape)
  }, [activeTheatrePanel, closeTheatrePanel])

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
    if (origin.moved) {
      anchor.current = origin.position
      onPlace?.(origin.position)
    }
  }

  function toggleBackground() {
    theatre?.setPanel(null)
    setOpenFor(null)
    background.toggle()
  }
  function finishBackground() {
    closeBackground()
    backgroundTrigger.current?.focus()
  }
  function toggle() {
    closeBackground()
    theatre?.setPanel(null)
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

  function toggleStagePanel() {
    closeBackground()
    setOpenFor(null)
    theatre.setPanel(theatre.panel ? null : 'position')
  }

  function preserveInputFocus(event) {
    if (document.activeElement?.matches('textarea')) event.preventDefault()
  }

  let wardrobeTitle = '切换服装'
  if (locked) wardrobeTitle = '回放结束后可更换服装'
  else if (!theatre && wardrobe?.role?.forms?.length > 1 && wardrobe.activeFormName)
    wardrobeTitle = '人格与服装（当前：' + wardrobe.activeFormName + '）'

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
        {theatre && (
          <Button
            ref={adjust}
            className="toolbar-section-end"
            variant="ghost"
            size="icon"
            aria-label="调整舞台"
            aria-expanded={Boolean(theatre.panel)}
            disabled={!theatre.actors.length}
            onClick={toggleStagePanel}
          >
            <Move size={16} />
          </Button>
        )}
        {!theatre && (
          <>
            <Button variant="ghost" size="icon" aria-label="放大角色" onClick={increase}>
              <ZoomIn />
            </Button>
            <Button variant="ghost" size="icon" aria-label="缩小角色" onClick={decrease}>
              <ZoomOut />
            </Button>
            <Button
              className="toolbar-reset toolbar-section-end"
              variant="ghost"
              size="icon"
              aria-label="重置角色视图"
              onClick={onReset}
            >
              <RotateCcw />
            </Button>
          </>
        )}
        <hr />
        <Button
          ref={trigger}
          variant="ghost"
          size="icon"
          aria-label="切换服装"
          title={wardrobeTitle}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? id : undefined}
          disabled={!wardrobe || locked}
          onClick={toggle}
        >
          <Shirt />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          ref={backgroundTrigger}
          aria-label="切换背景"
          title={locked ? '回放结束后可更换背景' : '切换背景'}
          aria-haspopup="dialog"
          aria-expanded={backgroundOpen}
          disabled={!background.available || locked}
          onClick={toggleBackground}
        >
          <Picture />
        </Button>
        {!story && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="全窗口舞台"
            title="全窗口舞台"
            onMouseDown={preserveInputFocus}
            onClick={onExpand}
          >
            <Maximize />
          </Button>
        )}
      </div>
      {backgroundOpen && (
        <BackgroundPicker {...background} panelRef={panel} onClose={finishBackground} />
      )}
      {theatre?.panel && (
        <section
          className="stage-wardrobe theatre-stage-panel"
          ref={panel}
          role="dialog"
          aria-label="调整舞台"
        >
          <header className="theatre-stage-header">
            {theatre.canFace ? (
              <div className="theatre-panel-tabs" role="group" aria-label="舞台设置">
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={theatre.panel === 'position'}
                  onClick={() => theatre.setPanel('position')}
                >
                  站位
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={theatre.panel === 'orientation'}
                  onClick={() => theatre.setPanel('orientation')}
                >
                  朝向
                </Button>
              </div>
            ) : (
              <h2 className="theatre-stage-title">调整站位</h2>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="theatre-stage-close"
              aria-label="完成舞台调整"
              onClick={() => theatre.setPanel(null)}
            >
              <X size={16} />
            </Button>
          </header>
          {theatre.panel === 'position' && (
            <>
              {theatre.selected == null ? (
                <p className="theatre-panel-hint">点击或拖动画面中的角色</p>
              ) : (
                <div className="theatre-position-body">
                  <div className="theatre-position-actor">
                    <strong>{theatre.actors[theatre.selected]?.displayName}</strong>
                    <span>拖动画面调整位置</span>
                  </div>
                  <div className="theatre-position-controls">
                    <div className="theatre-zoom-group" role="group" aria-label="缩放演员">
                      <Button
                        variant="outline"
                        size="icon-sm"
                        aria-label="放大选中演员"
                        onClick={increase}
                      >
                        <ZoomIn size={16} />
                      </Button>
                      <Button
                        variant="outline"
                        size="icon-sm"
                        aria-label="缩小选中演员"
                        onClick={decrease}
                      >
                        <ZoomOut size={16} />
                      </Button>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="theatre-position-reset"
                      onClick={onReset}
                    >
                      <RotateCcw size={14} aria-hidden="true" />
                      重置位置
                    </Button>
                  </div>
                </div>
              )}
              {theatre.degradedNote && (
                <p className="theatre-panel-note theatre-degraded-note">{theatre.degradedNote}</p>
              )}
            </>
          )}
          {theatre.canFace && theatre.panel === 'orientation' && (
            <div className="theatre-orientation-body">
              <div className="theatre-segmented" role="group" aria-label="角色朝向">
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={!theatre.inward}
                  onClick={() => theatre.face('screen')}
                >
                  面向屏幕
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={theatre.inward}
                  onClick={() => theatre.face('face-to-face')}
                >
                  面对面
                </Button>
              </div>
            </div>
          )}
        </section>
      )}
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
