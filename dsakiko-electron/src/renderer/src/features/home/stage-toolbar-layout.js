/** @typedef {{left: number, top: number, width: number, height: number}} StageRect 舞台局部坐标中的矩形。 */
const gap = 8
/** @typedef {{width: number, height: number, gripX: number, gripY: number}} ToolbarSize 工具栏尺寸及手柄中心相对工具栏的位置。 */
const turnDistance = 100
const rightClearance = 100

function clamp(value, min, max) {
  return Math.max(min, Math.min(value, max))
}

/**
 * @param {{x: number, y: number, orientation: 'horizontal'|'vertical'}} anchor 未约束的手柄中心和当前朝向。
 * @param {StageRect} stage 舞台边界。
 * @param {Record<'horizontal'|'vertical', ToolbarSize>} sizes 两个朝向下的实测尺寸。
 * @returns {{left: number, top: number, orientation: 'horizontal'|'vertical', x: number, y: number}} 舞台内工具栏位置和手柄中心。
 */
export function placeToolbar(anchor, stage, sizes) {
  const row = sizes.horizontal
  const column = sizes.vertical
  const rightRoom = stage.width - gap - (anchor.x - row.gripX + row.width)
  const bottomRoom = stage.height - gap - (anchor.y - column.gripY + column.height)
  const rowFits = row.width <= stage.width - 2 * gap
  const columnFits = column.height <= stage.height - stage.top - 2 * gap
  let orientation = anchor.orientation
  // 用指针请求的位置计算越界距离，工具栏贴边停住后仍可继续推动手柄。
  if (orientation === 'horizontal') {
    if (!rowFits || (rightRoom <= -turnDistance && columnFits)) orientation = 'vertical'
  } else if (
    rowFits &&
    (!columnFits || (bottomRoom <= -turnDistance && rightRoom >= rightClearance))
  ) {
    // 保留回转需要的横向余量，避免在右下角来回切换。
    orientation = 'horizontal'
  }

  const size = sizes[orientation]
  const left = clamp(anchor.x - size.gripX, gap, stage.width - size.width - gap)
  const top = clamp(anchor.y - size.gripY, stage.top + gap, stage.height - size.height - gap)
  return { left, top, orientation, x: left + size.gripX, y: top + size.gripY }
}

/**
 * @param {StageRect} stage 可用舞台区域。
 * @param {StageRect} toolbar 工具栏矩形。
 * @param {StageRect} trigger 服装按钮矩形。
 * @param {{width: number, height: number}} size 面板的理想大小。
 * @param {boolean} horizontal 工具栏是否横排。
 * @returns {{left: number, top: number, maxHeight: number}} 舞台内靠近工具栏的面板位置和可用高度。
 */
export function placeWardrobe(stage, toolbar, trigger, size, horizontal) {
  const minTop = stage.top + gap
  const maxBottom = stage.height - gap
  const maxLeft = stage.width - size.width - gap
  const aligned = clamp(horizontal ? toolbar.left : trigger.left, gap, maxLeft)
  const leftSide = toolbar.left - size.width - gap
  const rightSide = toolbar.left + toolbar.width + gap
  const candidates = {
    above: {
      left: aligned,
      min: minTop,
      max: toolbar.top - gap,
      top: toolbar.top - gap - size.height
    },
    below: {
      left: aligned,
      min: toolbar.top + toolbar.height + gap,
      max: Infinity,
      top: toolbar.top + toolbar.height + gap
    },
    left: { left: leftSide, min: minTop, max: Infinity, top: trigger.top },
    right: { left: rightSide, min: minTop, max: Infinity, top: trigger.top }
  }
  const sides = leftSide >= gap && rightSide > maxLeft ? ['left', 'right'] : ['right', 'left']
  const order = horizontal ? ['above', ...sides, 'below'] : [...sides, 'above', 'below']
  let best
  for (const side of order) {
    const { left, min, max, top } = candidates[side]
    if (left < gap || left > maxLeft) continue
    const end = Math.min(max, maxBottom)
    const maxHeight = end - min
    if (maxHeight <= 0) continue
    const result = {
      left,
      top: clamp(top, min, end - Math.min(size.height, maxHeight)),
      maxHeight
    }
    if (maxHeight >= size.height) return result
    if (!best || maxHeight > best.maxHeight) best = result
  }

  // 极小窗口的四周都放不下时，使用舞台可用高度供列表内部滚动。
  if (!best || best.maxHeight < 132)
    return { left: aligned, top: minTop, maxHeight: Math.max(0, maxBottom - minTop) }
  return best
}
