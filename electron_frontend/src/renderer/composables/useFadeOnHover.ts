import { ref, computed } from 'vue'

/**
 * 悬停隐藏功能（简化版 airi fade-on-hover）
 *
 * airi 原版使用了像素透明度检测 + Three.js hit test，
 * 这里简化为：鼠标进入窗口中间区域时判定为在模型上方。
 */
export function useFadeOnHover() {
  const enabled = ref(false)
  const mouseInWindow = ref(true)
  const mouseX = ref(0)
  const mouseY = ref(0)
  const windowWidth = ref(450)
  const windowHeight = ref(600)

  // 简化：鼠标在窗口中间 60% 区域时认为在模型上方
  const isOverModel = computed(() => {
    if (!mouseInWindow.value) return false
    const marginX = windowWidth.value * 0.2
    const marginY = windowHeight.value * 0.2
    return (
      mouseX.value > marginX &&
      mouseX.value < windowWidth.value - marginX &&
      mouseY.value > marginY &&
      mouseY.value < windowHeight.value - marginY
    )
  })

  const shouldFade = computed(() =>
    enabled.value && mouseInWindow.value && isOverModel.value
  )

  function toggle() {
    enabled.value = !enabled.value
  }

  function updateMousePosition(x: number, y: number) {
    mouseX.value = x
    mouseY.value = y
  }

  function setMouseInWindow(value: boolean) {
    mouseInWindow.value = value
  }

  return {
    enabled,
    shouldFade,
    toggle,
    updateMousePosition,
    setMouseInWindow,
  }
}
