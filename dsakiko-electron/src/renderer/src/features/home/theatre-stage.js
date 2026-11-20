/** @typedef {{ id: string, displayName: string }} TheatreActor */
/** @typedef {{ canFace: boolean, inward: boolean, panel: 'position' | 'orientation' | null, degradedNote: string }} TheatreStageState */

/**
 * 解析双人小剧场「调整舞台」浮窗的可用性与显示状态。
 * @param {object} options 当前小剧场的舞台能力与用户设定。
 * @param {TheatreActor[]} options.actors 舞台上的角色列表。
 * @param {boolean[] | null | undefined} options.sides 各角色当前服装是否支持内向站姿，首帧未就绪时为 null。
 * @param {'screen' | 'face-to-face' | undefined} options.orientation 本场保存的朝向偏好。
 * @param {'position' | 'orientation' | null | undefined} options.requestedPanel 用户当前打开的调整页签。
 * @returns {TheatreStageState} 经过能力校验与自动回退后的舞台工具状态。
 */
export function resolveTheatreStageState({ actors, sides, orientation, requestedPanel }) {
  // 舞台首帧场景能力尚未返回时暂不展示「朝向」页签，也不误报降级提示。
  const sidesReady =
    Array.isArray(sides) &&
    sides.length === actors.length &&
    sides.every((value) => typeof value === 'boolean')
  const canFace = actors.length === 2 && sidesReady && sides.every(Boolean)
  const unsupportedActors = sidesReady ? actors.filter((_, index) => !sides[index]) : []

  let panel = requestedPanel ?? null
  // 停留在「朝向」页签时若换装导致不再支持面对面，自动回到「站位」视图，避免空白面板。
  if (panel === 'orientation' && !canFace) {
    panel = 'position'
  }

  const inward = canFace && orientation === 'face-to-face'

  let degradedNote = ''
  if (!canFace && sidesReady && orientation === 'face-to-face' && unsupportedActors.length > 0) {
    const names = unsupportedActors.map((actor) => actor.displayName).join('、')
    degradedNote = `${names}的服装不支持面对面，已暂改为面向屏幕`
  }

  return { canFace, inward, panel, degradedNote }
}
