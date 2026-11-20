import { useRef, useState } from 'react'

/**
 * 舞台站位调整覆盖层，支持直接点选或按住拖拽调整左右演员位置。
 * @param {object} props 左右演员、选中槽位、舞台控制器及保存回调。
 * @returns {import('react').ReactElement} 仅编辑期间覆盖舞台的半屏选择区。
 */
export function TheatrePositions({ actors, selected, choose, stage, save }) {
  const drag = useRef(null)
  const [draggingSlot, setDraggingSlot] = useState(null)

  function start(event) {
    if (event.button !== 0) return
    const index = Number(event.currentTarget.dataset.slot)
    if (selected !== index) choose(index)
    const actor = stage.current?.actors[index]
    if (!actor?.surface.model) return
    drag.current = {
      index,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      layout: actor.layout(),
      changed: false
    }
    setDraggingSlot(index)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function move(event) {
    const origin = drag.current
    if (!origin || origin.pointerId !== event.pointerId) return
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
    const dx = event.clientX - origin.x
    const dy = event.clientY - origin.y
    if (!origin.changed && Math.hypot(dx, dy) < 3) return
    origin.changed = true
    stage.current.moveActor(origin.index, dx, dy, origin.layout)
  }

  function finish() {
    const origin = drag.current
    drag.current = null
    setDraggingSlot(null)
    if (origin?.changed) save(origin.index)
  }

  function activateByKeyboard(event) {
    // 指针点选已在 pointerdown 时触发，这里仅保留键盘唤醒选择。
    if (event.detail !== 0) return
    choose(Number(event.currentTarget.dataset.slot))
  }

  return (
    <div
      className="theatre-halves"
      role="group"
      aria-label="选择要调整的演员"
      data-has-selection={selected != null || undefined}
    >
      {actors.map((role, index) => (
        <button
          key={role.id}
          type="button"
          className="theatre-half"
          data-slot={index}
          data-dragging={draggingSlot === index || undefined}
          aria-label={`调整${role.displayName}的站位`}
          aria-pressed={selected === index}
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={finish}
          onLostPointerCapture={finish}
          onClick={activateByKeyboard}
        >
          <span className="theatre-half-frame" aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}
