/** @typedef {{scale: number, offsetX: number, offsetY: number}} ModelLayout 相对可见包围盒的高度倍数与设计坐标平移。 */
const defaults = {
  v2: { scale: 1, offsetX: 0, offsetY: 64 },
  v3: { scale: 1.875, offsetX: 0, offsetY: 64 }
}

/** @param {string} version SDK 版本。 @returns {ModelLayout} 独立默认值；V2 半身模型不套用 V3 全身高度。 */
export function defaultStageLayout(version) {
  return { ...(defaults[version] ?? defaults.v3) }
}

/** 本次运行内按资源身份保存视角；重置只影响当前模型。 */
export class StageLayouts {
  constructor() {
    this.values = new Map()
  }
  get(key, version) {
    return { ...(this.values.get(key) ?? defaultStageLayout(version)) }
  }
  set(key, value) {
    const layout = {
      scale: Math.max(0.2, Math.min(5, value.scale)),
      offsetX: Math.max(-2048, Math.min(2048, value.offsetX)),
      offsetY: Math.max(-2048, Math.min(2048, value.offsetY))
    }
    this.values.set(key, layout)
    return { ...layout }
  }
  reset(key, version) {
    this.values.delete(key)
    return this.get(key, version)
  }
}
