<script setup lang="ts">
import { refDebounced, useIntervalFn } from '@vueuse/core'
import { computed, reactive, ref, watch, inject, onMounted, type Ref } from 'vue'

import ControlButtonTooltip from './control-button-tooltip.vue'
import ControlButton from './control-button.vue'
import type { ElectronWindowState } from '../../composables/electronWindowState'

declare const electronAPI: {
  toggleDevTools: () => Promise<boolean>
  toggleAlwaysOnTop: () => Promise<boolean>
  getWindowState: () => Promise<{ alwaysOnTop: boolean; bounds: { x: number; y: number; width: number; height: number }; visible: boolean } | null>
  setIgnoreMouseEvents: (ignore: boolean, options?: { forward: boolean }) => Promise<void>
  closeWindow: () => Promise<void>
  hideWindow: () => Promise<void>
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
  try { alwaysOnTop.value = await electronAPI.toggleAlwaysOnTop() } catch {}
}

const fadeOnHover = inject<Ref<boolean>>('fadeOnHoverEnabled', ref(false))
const toggleFadeOnHover = inject<() => void>('toggleFadeOnHover', () => {})
const sendUiIntent = inject<(intent: string) => boolean>('sendUiIntent', () => false)
const bridgeReady = inject<Ref<boolean>>('bridgeReady', ref(false))
interface SubtitleSettings {
  enabled: boolean
  fontSize: number
  bottomOffset: number
  maxWidth: number
  textColor: string
  backgroundColor: string
  backgroundOpacity: number
}
const subtitleSettings = inject<SubtitleSettings>('subtitleSettings', reactive({
  enabled: true, fontSize: 16, bottomOffset: 64, maxWidth: 80,
  textColor: '#D4D4D4', backgroundColor: '#262626', backgroundOpacity: 0.8,
}))
const resetSubtitleSettings = inject<() => void>('resetSubtitleSettings', () => {})
const subtitleStylesExpanded = ref(false)
const isDevelopment = import.meta.env.DEV
function previewBackgroundColor(color: string, opacity: number) {
  const value = Number.parseInt(color.slice(1), 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${opacity})`
}
const subtitlePreviewStyle = computed(() => ({
  fontSize: `${subtitleSettings.fontSize}px`,
  maxWidth: `${subtitleSettings.maxWidth}%`,
  color: subtitleSettings.textColor,
  backgroundColor: previewBackgroundColor(subtitleSettings.backgroundColor, subtitleSettings.backgroundOpacity),
  bottom: `${8 + (subtitleSettings.bottomOffset - 24) * 40 / 136}px`,
}))
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
function closeWindow() { void electronAPI.closeWindow() }
function toggleSettings() {
  settingsOpen.value = !settingsOpen.value
  setOverlay('settings', settingsOpen.value)
}
function closeSettings() {
  settingsOpen.value = false
  setOverlay('settings', false)
}

function hideWindow() { void electronAPI.hideWindow() }

function startVoiceInput(event: PointerEvent) {
  if (isVoiceRecording.value) return
  if (!sendUiIntent('start_voice_input')) return
  isVoiceRecording.value = true
  // Keep receiving pointerup even when the cursor leaves this compact control.
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
}

function stopVoiceInput(event?: PointerEvent) {
  if (!isVoiceRecording.value) return
  isVoiceRecording.value = false
  if (event?.currentTarget instanceof HTMLElement && event.currentTarget.hasPointerCapture?.(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId)
  }
  sendUiIntent('stop_voice_input')
}

onMounted(async () => {
  try {
    const state = await electronAPI.getWindowState()
    if (state) alwaysOnTop.value = state.alwaysOnTop
  } catch {}
})
</script>

<template>
  <div ref="islandRef" class="island-root" fixed bottom-2 right-2>
    <div class="island-stack" flex flex-col items-end gap-1>
      <Transition
        enter-active-class="transition-all duration-500 cubic-bezier(0.32, 0.72, 0, 1)"
        leave-active-class="transition-all duration-400 cubic-bezier(0.32, 0.72, 0, 1)"
        enter-from-class="opacity-0 translate-y-8 scale-90 blur-sm"
        leave-to-class="opacity-0 translate-y-8 scale-90 blur-sm"
      >
        <div v-if="expanded" mb-2 flex flex-col gap-1 class="expanded-panel" :class="{ 'has-settings': settingsOpen }">
          <Transition
            enter-active-class="transition-all duration-200 ease-out"
            leave-active-class="transition-all duration-150 ease-in"
            enter-from-class="opacity-0 -translate-y-1"
            leave-to-class="opacity-0 -translate-y-1"
          >
            <section v-if="settingsOpen" mb-1 rounded-xl p-2 class="settings-panel bg-neutral-100/95 shadow-xl shadow-black/15 backdrop-blur-md dark:bg-neutral-900/95 dark:shadow-black/30">
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

              <div class="settings-content" flex flex-col gap-2>
                <div border-b border="neutral-200/70 dark:neutral-800/70" pb-2>
                  <div mb-1 text-sm font-medium text="neutral-800 dark:neutral-100">字幕</div>
                  <div flex items-center justify-between gap-3 px-1 py-1 text-sm text="neutral-800 dark:neutral-200">
                    <span>显示字幕</span>
                    <label class="toggle-switch" aria-label="显示字幕">
                      <input v-model="subtitleSettings.enabled" type="checkbox">
                      <span class="toggle-track" aria-hidden="true"><span class="toggle-thumb" /></span>
                    </label>
                  </div>
                  <button
                    type="button"
                    class="subtitle-style-toggle"
                    :aria-expanded="subtitleStylesExpanded"
                    :disabled="!subtitleSettings.enabled"
                    @click="subtitleStylesExpanded = !subtitleStylesExpanded"
                  >
                    <span>字幕样式</span>
                    <div i-solar:alt-arrow-down-outline size-4 transition-transform duration-200 :class="{ 'rotate-180': subtitleStylesExpanded }" />
                  </button>
                  <div v-if="subtitleSettings.enabled && subtitleStylesExpanded" mt-2 flex flex-col gap-2>
                    <div class="subtitle-preview" aria-label="字幕预览">
                      <div class="subtitle-preview-bubble" :style="subtitlePreviewStyle">字幕预览文本</div>
                    </div>
                    <label class="setting-row" flex items-center justify-between gap-2 text-xs text="neutral-600 dark:neutral-300">字号 {{ subtitleSettings.fontSize }} px
                      <input v-model.number="subtitleSettings.fontSize" type="range" min="12" max="28" step="1">
                    </label>
                    <label class="setting-row" flex items-center justify-between gap-2 text-xs text="neutral-600 dark:neutral-300">垂直位置 {{ subtitleSettings.bottomOffset }} px
                      <input v-model.number="subtitleSettings.bottomOffset" type="range" min="24" max="160" step="1">
                    </label>
                    <label class="setting-row" flex items-center justify-between gap-2 text-xs text="neutral-600 dark:neutral-300">最大宽度 {{ subtitleSettings.maxWidth }}%
                      <input v-model.number="subtitleSettings.maxWidth" type="range" min="40" max="95" step="1">
                    </label>
                    <label class="setting-row" flex items-center justify-between gap-2 text-xs text="neutral-600 dark:neutral-300">文字颜色
                      <input v-model="subtitleSettings.textColor" type="color">
                    </label>
                    <label class="setting-row" flex items-center justify-between gap-2 text-xs text="neutral-600 dark:neutral-300">背景颜色
                      <input v-model="subtitleSettings.backgroundColor" type="color">
                    </label>
                    <label class="setting-row" flex items-center justify-between gap-2 text-xs text="neutral-600 dark:neutral-300">背景透明度 {{ Math.round(subtitleSettings.backgroundOpacity * 100) }}%
                      <input v-model.number="subtitleSettings.backgroundOpacity" type="range" min="0" max="0.95" step="0.05">
                    </label>
                    <button type="button" self-start rounded-lg px-2 py-1 text-xs transition-colors hover="bg-neutral-200/70 dark:bg-neutral-800/70" @click="resetSubtitleSettings">恢复默认</button>
                  </div>
                </div>
                <button
                  type="button"
                  flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover="bg-neutral-200/70 dark:bg-neutral-800/70"
                  @click="refreshWindow"
                >
                  <span text-sm text="neutral-800 dark:neutral-200">重载 Electron</span>
                  <div i-solar:refresh-linear size-4 text="neutral-700 dark:neutral-300" />
                </button>
                <div px-2 text-xs text="neutral-500 dark:neutral-400">当前回复可能会被中断</div>
              </div>
            </section>
          </Transition>

          <div class="controls-shell rounded-2xl p-2 backdrop-blur-md bg-neutral-100/95 shadow-2xl shadow-black/20 dark:bg-neutral-900/95 dark:shadow-black/30">
          <div class="button-panel" grid grid-cols-3 gap-2>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleSettings">
                <div i-lucide:package :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>{{ settingsOpen ? '收起本地设置' : '本地设置' }}</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="sendUiIntent('open_python_settings')">
                <div i-lucide:settings :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>打开 Python 设置</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip v-if="isDevelopment" disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" @click="toggleDevToolsHandler">
                <div i-solar:code-bold-duotone :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>DevTools</template>
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
              <ControlButton :button-style="adjustStyleClasses.button" @click="hideWindow()">
                <div i-solar:eye-closed-outline :class="adjustStyleClasses.icon" text="neutral-800 dark:neutral-300" />
              </ControlButton>
              <template #tooltip>隐藏到托盘</template>
            </ControlButtonTooltip>
            <ControlButtonTooltip disable-hoverable-content>
              <ControlButton :button-style="adjustStyleClasses.button" hover:bg-red-500 hover:text-white @click="closeWindow()">
                <div i-solar:close-circle-outline :class="adjustStyleClasses.icon" />
              </ControlButton>
              <template #tooltip>退出 Electron</template>
            </ControlButtonTooltip>
          </div>
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
          <template #tooltip>{{ isVoiceRecording ? '正在录音，松开后识别' : bridgeReady ? '按住录音，松开后识别' : '后端未连接' }}</template>
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

<style scoped>
.island-root { box-sizing: border-box; height: calc(100vh - .5rem); max-height: calc(100vh - .5rem); }
.island-stack { height: 100%; min-height: 0; justify-content: flex-end; }
.expanded-panel { width: max-content; max-width: calc(100vw - 1rem); min-height: 0; align-items: flex-end; }
.expanded-panel.has-settings { height: 100%; }
.controls-shell { width: max-content; max-width: 100%; flex: none; }
.button-panel { width: max-content; max-width: 100%; flex: none; }
.settings-panel {
  width: min(22rem, calc(100vw - 1rem));
  max-width: calc(100vw - 1rem);
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.settings-content {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: .25rem;
}

.setting-row { flex-wrap: wrap; }
.setting-row input[type='range'] {
  min-width: 0;
  width: 11rem;
  max-width: 100%;
  flex: 1 1 8rem;
}
.setting-row input[type='color'] {
  width: 3rem;
  max-width: 100%;
  flex: 0 1 3rem;
}

.toggle-switch {
  position: relative;
  display: inline-flex;
  width: 2.5rem;
  height: 1.4rem;
  flex: none;
  cursor: pointer;
}

.toggle-switch input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.toggle-track {
  display: flex;
  width: 100%;
  height: 100%;
  align-items: center;
  padding: .15rem;
  border-radius: 999px;
  background: rgba(100, 116, 139, .55);
  transition: background-color .18s ease;
}

.toggle-thumb {
  width: 1.1rem;
  height: 1.1rem;
  border-radius: 50%;
  background: #f8fafc;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .3);
  transition: transform .18s ease;
}

.toggle-switch input:checked + .toggle-track {
  background: #60a5fa;
}

.toggle-switch input:checked + .toggle-track .toggle-thumb {
  transform: translateX(1.1rem);
}

.toggle-switch input:focus-visible + .toggle-track {
  outline: 2px solid #93c5fd;
  outline-offset: 2px;
}

.subtitle-style-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  margin-top: .15rem;
  padding: .35rem .25rem;
  border: 0;
  border-top: 1px solid rgba(148, 163, 184, .24);
  color: inherit;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: .75rem;
  text-align: left;
}

.subtitle-style-toggle:disabled {
  cursor: not-allowed;
  opacity: .45;
}

.subtitle-preview {
  position: relative;
  height: 8rem;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, .42);
  border-radius: .5rem;
  background: rgba(15, 23, 42, .5);
}

.subtitle-preview-bubble {
  position: absolute;
  left: 50%;
  box-sizing: border-box;
  padding: .35rem .65rem;
  text-align: center;
  border-radius: .5rem;
  transform: translateX(-50%);
  width: 80%;
  max-width: calc(100% - 1rem);
  overflow-wrap: anywhere;
}

@media (max-width: 260px) {
  .settings-panel { width: calc(100vw - 1rem); }
  .setting-row { display: flex; align-items: flex-start; flex-direction: column; }
  .setting-row input[type='range'] { width: 100%; flex-basis: auto; }
  .setting-row input[type='color'] { align-self: flex-end; }
}
</style>
