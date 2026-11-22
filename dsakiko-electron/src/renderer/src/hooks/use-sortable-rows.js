import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/** @param {string[]} left 第一组 ID。 @param {string[]} right 第二组 ID。 @returns {boolean} 两组 ID 及其顺序是否一致。 */
export function sameOrder(left, right) {
  return left.length === right.length && left.every((id, index) => id === right[index])
}

/**
 * @template {{id: string}} T
 * @param {T[]} items 当前列表。
 * @param {string[]|null} order 尚待同步的顺序。
 * @returns {T[]} 按暂存顺序排列的当前列表，期间新增的条目保留在末尾。
 */
export function orderItems(items, order) {
  if (!order) return items
  const byId = new Map(items.map((item) => [item.id, item]))
  const ordered = []
  for (const id of order) {
    const item = byId.get(id)
    if (!item) continue
    ordered.push(item)
    byId.delete(id)
  }
  return ordered.concat([...byId.values()])
}

/** @typedef {{id: string, top: number, height: number}} SortRow 按下时，条目在列表中的布局。 */
/** @typedef {{id: string, order: string[], offsets: Map<string, number>, slotTop: number, height: number}} SortPreview 拖动期间的插槽与行偏移。 */
/**
 * @typedef {object} RowDrag
 * @property {number} pointerId 拖动使用的指针。
 * @property {HTMLElement} handle 捕获指针的元素，可以是整行。
 * @property {SortRow} row 正在拖动的条目。
 * @property {SortRow[]} rows 按下时所有可见条目的布局。
 * @property {number} gap 条目之间的间距。
 * @property {number} startY 起始位置相对于列表的纵坐标。
 * @property {number} clientY 最后一次指针事件的视口纵坐标。
 * @property {SortPreview|null} preview 超过拖动阈值后当前的插槽。
 * @property {AbortController} listeners 窗口级手势监听的生命周期。
 * @property {number|null} scrollFrame 手势期间等待执行的滚动帧。
 */

function reorderByStep(ids, id, step) {
  const index = ids.indexOf(id)
  const target = index + step
  if (index < 0 || target < 0 || target >= ids.length) return null
  const next = [...ids]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  return next
}

function dragPreview(active, pointerY) {
  // 按下时的布局用于命中测试，让位动画本身不会推移插入点。
  const others = active.rows.filter((row) => row !== active.row)
  const index = others.filter((row) => pointerY > row.top + row.height / 2).length
  others.splice(index, 0, active.row)
  let top = 0
  let slotTop = 0
  const offsets = new Map()
  for (const row of others) {
    if (row === active.row) slotTop = top
    offsets.set(row.id, top - row.top)
    top += row.height + active.gap
  }
  const bottom = top - active.gap - active.row.height
  const liftedTop = Math.max(0, Math.min(bottom, active.row.top + pointerY - active.startY))
  offsets.set(active.row.id, liftedTop - active.row.top)
  return {
    id: active.row.id,
    order: others.map((row) => row.id),
    offsets,
    slotTop,
    height: active.row.height
  }
}

function releaseDrag(active) {
  active.listeners.abort()
  if (active.scrollFrame !== null) cancelAnimationFrame(active.scrollFrame)
  if (active.handle.hasPointerCapture(active.pointerId))
    active.handle.releasePointerCapture(active.pointerId)
}

function edgeScrollSpeed(clientY, viewport) {
  const edge = Math.min(36, viewport.height / 3)
  if (clientY < viewport.top + edge) return -Math.min(13, (viewport.top + edge - clientY) / 3)
  if (clientY > viewport.bottom - edge) return Math.min(13, (clientY - viewport.bottom + edge) / 3)
  return 0
}

/**
 * @param {{stackRef: import('react').RefObject<HTMLElement|null>, ids: string[], disabled: boolean, onReorder: (ids: string[]) => void, scrollRef?: import('react').RefObject<HTMLElement|null>}} options 列表容器、顺序、提交入口与可选的滚动视口。
 * @returns {{dragState: SortPreview|null, startDrag: (event: import('react').PointerEvent, id: string) => void, moveDrag: import('react').PointerEventHandler, dropDrag: import('react').PointerEventHandler, cancelDrag: import('react').PointerEventHandler, handleSortKeyDown: (event: import('react').KeyboardEvent, id: string) => void, suppressDragClick: import('react').MouseEventHandler}} 行与拖动目标共用的交互处理。
 */
export function useSortableRows({ stackRef, ids, disabled, onReorder, scrollRef }) {
  const dragRef = useRef(null)
  const settleFrom = useRef(null)
  const refocus = useRef(null)
  const suppressClick = useRef(false)
  const [dragState, setDragState] = useState(null)

  useEffect(
    () => () => {
      const active = dragRef.current
      if (active) releaseDrag(active)
    },
    []
  )

  useLayoutEffect(() => {
    const previous = settleFrom.current
    settleFrom.current = null
    if (previous && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      for (const card of stackRef.current?.querySelectorAll('[data-sort-id]') ?? []) {
        const offset = previous.get(card.dataset.sortId) - card.getBoundingClientRect().top
        if (Number.isFinite(offset) && Math.abs(offset) > 0.5) {
          card.animate([{ transform: 'translateY(' + offset + 'px)' }, { transform: 'none' }], {
            duration: 150,
            easing: 'ease-out'
          })
        }
      }
    }
    if (!refocus.current) return
    const { handle, reveal } = refocus.current
    refocus.current = null
    if (handle.isConnected) {
      handle.focus({ preventScroll: true })
      if (reveal) handle.scrollIntoView({ block: 'nearest' })
    }
  }, [dragState, ids, stackRef])

  function startDrag(event, id) {
    if (event.button !== 0 || dragRef.current) return
    suppressClick.current = false
    if (disabled || ids.length < 2) return
    const stack = stackRef.current
    const stackTop = stack.getBoundingClientRect().top
    const rows = [...stack.querySelectorAll('[data-sort-id]')].map((card) => {
      for (const animation of card.getAnimations()) animation.cancel()
      const rect = card.getBoundingClientRect()
      return { id: card.dataset.sortId, top: rect.top - stackTop, height: rect.height }
    })
    const row = rows.find((item) => item.id === id)
    if (!row) return
    const handle = event.currentTarget
    /** @type {RowDrag} */
    const active = {
      row,
      rows,
      pointerId: event.pointerId,
      handle,
      gap: Number.parseFloat(getComputedStyle(stack).rowGap) || 0,
      startY: event.clientY - stackTop,
      clientY: event.clientY,
      preview: null,
      listeners: new AbortController(),
      scrollFrame: null
    }
    dragRef.current = active
    handle.setPointerCapture(event.pointerId)
    const options = { signal: active.listeners.signal, capture: true }
    window.addEventListener('blur', cancelDrag, { signal: active.listeners.signal })
    window.addEventListener('lostpointercapture', cancelDrag, options)
    window.addEventListener(
      'keydown',
      (key) => {
        if (key.key !== 'Escape' || !active.preview) return
        key.preventDefault()
        key.stopPropagation()
        cancelDrag()
      },
      options
    )
    window.addEventListener(
      'scroll',
      () => {
        if (active.preview) previewAt(active, active.clientY)
      },
      options
    )
  }

  function previewAt(active, clientY) {
    const stack = stackRef.current
    if (!stack) return
    active.clientY = clientY
    const y = clientY - stack.getBoundingClientRect().top
    if (!active.preview && Math.abs(y - active.startY) < 5) return
    active.preview = dragPreview(active, y)
    setDragState(active.preview)
  }

  function scrollAtEdge(active) {
    if (!active.preview || active.scrollFrame !== null || !scrollRef?.current) return
    const viewport = scrollRef.current
    if (!edgeScrollSpeed(active.clientY, viewport.getBoundingClientRect())) return
    active.scrollFrame = requestAnimationFrame(() => {
      active.scrollFrame = null
      const before = viewport.scrollTop
      viewport.scrollTop += edgeScrollSpeed(active.clientY, viewport.getBoundingClientRect())
      if (viewport.scrollTop !== before) scrollAtEdge(active)
    })
  }

  function moveDrag(event) {
    const active = dragRef.current
    if (!active || active.pointerId !== event.pointerId) return
    previewAt(active, event.clientY)
    scrollAtEdge(active)
  }

  function rememberPositions() {
    const cards = [...(stackRef.current?.querySelectorAll('[data-sort-id]') ?? [])]
    settleFrom.current = new Map(
      cards.map((card) => [card.dataset.sortId, card.getBoundingClientRect().top])
    )
  }

  function finishDrag(active) {
    if (active.preview) {
      rememberPositions()
      // 捕获指针的按钮在松手或取消后仍可能收到一次 click。
      suppressClick.current = true
    }
    dragRef.current = null
    releaseDrag(active)
    setDragState(null)
  }

  function cancelDrag(event) {
    const active = dragRef.current
    if (!active || (event?.pointerId !== undefined && event.pointerId !== active.pointerId)) return
    finishDrag(active)
  }

  function dropDrag(event) {
    const active = dragRef.current
    if (!active || active.pointerId !== event.pointerId) return
    const stack = stackRef.current
    const order =
      active.preview &&
      stack &&
      dragPreview(active, event.clientY - stack.getBoundingClientRect().top).order
    finishDrag(active)
    const startOrder = active.rows.map((row) => row.id)
    if (disabled || !order || !sameOrder(startOrder, ids) || sameOrder(order, ids)) return
    if (document.activeElement === active.handle)
      refocus.current = { handle: active.handle, reveal: false }
    onReorder(order)
  }

  function handleSortKeyDown(event, id) {
    if (disabled || dragRef.current || ids.length < 2) return
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    event.preventDefault()
    const step = event.key === 'ArrowUp' ? -1 : 1
    const order = reorderByStep(ids, id, step)
    if (!order) return
    rememberPositions()
    refocus.current = { handle: event.currentTarget, reveal: true }
    onReorder(order)
  }

  function suppressDragClick(event) {
    if (!suppressClick.current || event.detail === 0) return
    suppressClick.current = false
    event.preventDefault()
    event.stopPropagation()
  }

  return {
    dragState,
    startDrag,
    moveDrag,
    dropDrag,
    cancelDrag,
    handleSortKeyDown,
    suppressDragClick
  }
}
