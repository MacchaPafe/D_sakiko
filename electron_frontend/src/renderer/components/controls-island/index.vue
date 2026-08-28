<script setup lang="ts">
import { refDebounced, useIntervalFn } from '@vueuse/core'
import { computed, reactive, ref, watch, inject } from 'vue'

import ControlButtonTooltip from './control-button-tooltip.vue'
import ControlButton from './control-button.vue'
import type { ElectronWindowState } from '../../composables/electronWindowState'

declare const electronAPI: {
  toggleDevTools: () => Promise<boolean>
  toggleAlwaysOnTop: () => Promise<boolean>
  setIgnoreMouseEvents: (ignore: boolean, options?: { forward: boolean }) => Promise<void>
}

const isDark = ref(document.documentElement.classList.contains('dark'))
function toggleDark() {
  isDark.value = !isDark.value
  if (isDark.value) {
    document.documentElement.classList.add('dark')
    document.documentElement.classList.remove('light')
  } else {
    document.documentElement.classList.add('light')
    document.documentElement.classList.remove('dark')
  }
  localStorage.setItem('saki-theme', isDark.value ? 'dark' : 'light')
}

const expanded = ref(false)
const settingsOpen = ref(false)
const islandRef = ref<HTMLElement>()

const blockingOverlays = reactive(new Set<string>())
const isBlocked = computed(() => blockingOverlays.size > 0)

function setOverlay(key: string, active: boolean) {
  if (active) { blockingOverlays.add(key); return }
  blockingOverlays.delete(key)
}

defineExpose({
  get hearingDialogOpen() { return blockingOverlays.has('hearing') },
  set hearingDialogOpen(v: boolean) { setOverlay('hearing', v) },
})

const { isOutside } = (() => {
  const isOutside = ref(true)
  const handler = (e: MouseEvent) => {
    if (!islandRef.value) return
    const rect = islandRef.value.getBoundingClientRect()
    isOutside.value = !(
      e.clientX >= rect.left && e.clientX <= rect.right &&
      e.clientY >= rect.top && e.clientY <= rect.bottom
    )
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('mousemove', handler)
  }
  return { isOutside }
})()

const isOutsideAfter2seconds = refDebounced(isOutside, 1500)

watch(isOutsideAfter2seconds, (outside) => {
  if (outside && expanded.value && !isBlocked.value) { expanded.value = false }
})

watch(expanded, (isExpanded) => {
  if (!isExpanded) {
    settingsOpen.value = false
    blockingOverlays.clear()
  }
})

useIntervalFn(() => {
  if (expanded.value && isOutside.value && !isBlocked.value) { expanded.value = false }
}, 1500)

const alwaysOnTop = ref(true)
async function toggleAlwaysOnTop() {
  try { const r = await electronAPI.toggleAlwaysOnTop(); alwaysOnTop.value = r } catch { alwaysOnTop.value = !alwaysOnTop.value }
}

const fadeOnHover = inject<ReturnType<typeof ref<boolean>>>('fadeOnHoverEnabled', ref(false))
const toggleFadeOnHover = inject<() => void>('toggleFadeOnHover', () => {})
const sendRendererIntent = inject<(intent: string) => void>('sendRendererIntent', () => {})
const windowState = inject<ElectronWindowState>('electronWindowState', reactive({
  cursor: { x: 0, y: 0 },
  bounds: { x: 0, y: 0, width: 0, height: 0 },
}))
const isVoiceRecording = ref(false)

// ── 鼠标穿透（照搬 airi 逻辑）──
// fadeOnHover ON + 鼠标在面板外 → 穿透；鼠标在面板内或面板展开 → 不穿透
const _cursorInsideIsland = ref(false)
const isOutsideFor250Ms = refDebounced(computed(() => !_cursorInsideIsland.value || isOutside.value), 250)
const isOverResizeHandle = computed(() => {
  const x = windowState.cursor.x - windowState.bounds.x
  const y = windowState.cursor.y - windowState.bounds.y
  const edge = 12
  return windowState.bounds.width > 0 && windowState.bounds.height > 0
    && x >= 0 && x <= windowState.bounds.width
    && y >= 0 && y <= windowState.bounds.height
    && (x <= edge || x >= windowState.bounds.width - edge
      || y <= edge || y >= windowState.bounds.height - edge)
})

function updateCursorInsideIsland() {
  const el = islandRef.value
  if (!el || windowState.bounds.width <= 0 || windowState.bounds.height <= 0) return
  const rect = el.getBoundingClientRect()
  _cursorInsideIsland.value = windowState.cursor.x >= windowState.bounds.x + rect.left
    && windowState.cursor.x <= windowState.bounds.x + rect.right
    && windowState.cursor.y >= windowState.bounds.y + rect.top
    && windowState.cursor.y <= windowState.bounds.y + rect.bottom
}

watch(
  () => [windowState.cursor.x, windowState.cursor.y, windowState.bounds.x, windowState.bounds.y,
    windowState.bounds.width, windowState.bounds.height],
  updateCursorInsideIsland,
  { immediate: true },
)

async function syncPenetrate() {
  const on = !!fadeOnHover.value && !expanded.value && isOutsideFor250Ms.value && !isOverResizeHandle.value
  try { await electronAPI.setIgnoreMouseEvents(on, { forward: true }) } catch {}
}

watch([fadeOnHover, expanded, isOutsideFor250Ms, isOverResizeHandle], syncPenetrate)

watch(fadeOnHover, (on) => {
  if (!on) {
    // 关闭时确保恢复交互
    electronAPI.setIgnoreMouseEvents(false, { forward: true })
  }
})

const adjustStyleClasses = computed(() => {
  const icon = 'size-5'
  const padding = 'p-2'
  return { icon, padding, button: padding }
})

function refreshWindow() { window.location.reload() }
async function toggleDevToolsHandler() { try { await electronAPI.toggleDevTools() } catch {} }
function closeWindow() { window.close() }
function toggleSettings() {
  settingsOpen.value = !settingsOpen.value
  setOverlay('settings', settingsOpen.value)
}
function closeSettings() {
  settingsOpen.value = false
  setOverlay('settings', false)
}

function startVoiceInput(event: PointerEvent) {
  if (isVoiceRecording.value) return
  isVoiceRecording.value = true
  // Keep receiving pointerup even when the cursor leaves this compact control.
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
  sendRendererIntent('start_voice_input')
}

function stopVoiceInput(event?: PointerEvent) {
  if (!isVoiceRecording.value) return
  isVoiceRecording.value = false
  if (event?.currentTarget instanceof HTMLElement && event.currentTarget.hasPointerCapture?.(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId)
  }
  sendRendererIntent('stop_voice_input')
}
</script>

<template>
  <div ref="islandRef" fixed bottom-2 right-2>
    <div flex flex-col items-end gap-1>
      <Transition
        enter-active-class="transition-all duration-500 cubic-bezier(0.32, 0.72, 0, 1)"
        leave-active-class="transition-all duration-400 cubic-bezier(0.32, 0.72, 0, 1)"
        enter-from-class="opacity-0 translate-y-8 scale-90 blur-sm"
        leave-to-class="opacity-0 translate-y-8 scale-90 blur-sm"
      >
        <div v-if="expanded" mb-2 flex flex-col gap-1 rounded-2xl p-2 backdrop-blur-xl class="bg-neutral-100/80 shadow-2xl shadow-black/20 dark:bg-neutral-900/80">
          <Transition
            enter-active-class="transition-all duration-200 ease-out"
            leave-active-class="transition-all duration-150 ease-in"
            enter-from-class="opacity-0 -translate-y-1"
            leave-to-class="opacity-0 -translate-y-1"
          >
            <section v-if="settingsOpen" mb-1 rounded-xl p-2 class="bg-neutral-50/70 dark:bg-neutral-950/30">
              <div mb-2 flex items-center justify-between gap-3>
                <div>
                  <div text-sm font-medium text="neutral-800 dark:neutral-100">本地设置</div>
                  <div text-xs text="neutral-500 dark:neutral-400">仅控制当前桌面窗口</div>
                </div>
                <button
                  type="button"
                  rounded-lg p-1.5 transition-colors hover="bg-neutral-200/80 dark:bg-neutral-800"
                  aria-label="关闭设置"
                  @click="closeSettings"
                >
                  <div i-solar:close-circle-outline size-4 text="neutral-700 dark:neutral-300" />
                </button>
              </div>

              <div flex flex-col gap-1>
                <button
                  type="button"
                  flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover="bg-neutral-200/70 dark:bg-neutral-800/70"
                  @click="refreshWindow"
                >
                  <span text-sm text="neutral-800 dark:neutral-200">刷新窗口</span>
                  <div i-solar:refresh-linear size-4 text="neutral-700 dark:neutral-300" />
                </button>
              </div>
            </section>
          </Transition>

          <div grid grid-cols-3 gap-2>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleSettings">
                <div i-lucide:package :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>{{ settingsOpen ? '收起窗口菜单' : '窗口菜单' }}</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="sendRendererIntent('open_python_settings')">
                <div i-lucide:settings :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>打开 Python 设置</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleDevToolsHandler">
                <div i-solar:code-bold-duotone :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>Vue DevTools</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="refreshWindow">
                <div i-solar:refresh-linear :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>刷新窗口</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleDark()">
                <Transition name="fade" mode="out-in">
                  <div v-if="isDark" i-solar:moon-outline :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                  <div v-else i-solar:sun-2-outline :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                </Transition>
              </ControlButton>
              <template #tooltip>{{ isDark ? '切换亮色模式' : '切换暗色模式' }}</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleAlwaysOnTop()">
                <div v-if="alwaysOnTop" i-solar:pin-bold :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                <div v-else i-solar:pin-linear :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300 opacity-50" />
              </ControlButton>
              <template #tooltip>{{ alwaysOnTop ? '取消置顶' : '窗口置顶' }}</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton
                :button-style="adjustStyleClasses.button"
                :class="{ 'border-primary-300/70 shadow-[0_10px_24px_rgba(0,0,0,0.22)]': fadeOnHover }"
                @click="toggleFadeOnHover()"
              >
                <Transition name="fade" mode="out-in">
                  <div v-if="fadeOnHover" i-ph:eye :class="adjustStyleClasses.icon" text="primary-700 dark:primary-300" />
                  <div v-else i-ph:eye-slash :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
                </Transition>
              </ControlButton>
              <template #tooltip>{{ fadeOnHover ? '禁用悬停隐藏' : '启用悬停隐藏' }}</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" hover:bg-red-500 hover:text-white @click="closeWindow()">
                <div i-solar:close-circle-outline :class="adjustStyleClasses.icon" />
              </ControlButton>
              <template #tooltip>关闭</template>
            </ControlButtonTooltip>
          </div>
        </div>
      </Transition>

      <div flex flex-col gap-1>
        <ControlButtonTooltip side="left">
          <ControlButton :button-style="adjustStyleClasses.button" @click="expanded = !expanded">
            <div
              :class="[adjustStyleClasses.icon, expanded ? 'rotate-180' : 'rotate-0']"
              i-solar:alt-arrow-up-line-duotone scale-110 transition-all duration-300
              text="neutral-800 dark:neutral-300"
            />
          </ControlButton>
          <template #tooltip>{{ expanded ? '收起' : '展开' }}</template>
        </ControlButtonTooltip>
        <ControlButtonTooltip side="left">
          <ControlButton
            :button-style="adjustStyleClasses.button"
            :aria-pressed="isVoiceRecording"
            @pointerdown.prevent="startVoiceInput"
            @pointerup.prevent="stopVoiceInput"
            @pointercancel="stopVoiceInput"
            @lostpointercapture="stopVoiceInput"
          >
            <div v-if="isVoiceRecording" i-ph:microphone :class="adjustStyleClasses.icon" text="primary-700 dark:primary-300" />
            <div v-else i-ph:microphone-slash :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
          </ControlButton>
          <template #tooltip>{{ isVoiceRecording ? '正在录音，松开后识别' : '按住录音，松开后识别' }}</template>
        </ControlButtonTooltip>
        <ControlButtonTooltip side="left">
          <ControlButton :button-style="adjustStyleClasses.button" cursor-move style="-webkit-app-region: drag">
            <div i-ph:arrows-out-cardinal :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
          </ControlButton>
          <template #tooltip>拖动移动窗口</template>
        </ControlButtonTooltip>
      </div>
    </div>
  </div>
</template>
