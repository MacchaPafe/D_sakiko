<script setup lang="ts">
declare const electronAPI: {
  resizeWindow: (dx: number, dy: number, dir: string) => Promise<void>
  setIgnoreMouseEvents: (ignore: boolean, options?: { forward: boolean }) => Promise<void>
}

function handleResizeStart(event: MouseEvent, direction: string) {
  event.preventDefault()
  event.stopPropagation()
  void electronAPI.setIgnoreMouseEvents(false, { forward: true })
  let startX = event.screenX
  let startY = event.screenY
  let frame: number | null = null
  let pendingDeltaX = 0
  let pendingDeltaY = 0

  function flushResize() {
    frame = null
    if (pendingDeltaX === 0 && pendingDeltaY === 0) return
    const deltaX = pendingDeltaX
    const deltaY = pendingDeltaY
    pendingDeltaX = 0
    pendingDeltaY = 0
    void electronAPI.resizeWindow(deltaX, deltaY, direction)
  }

  function scheduleResize() {
    if (frame !== null) return
    frame = requestAnimationFrame(flushResize)
  }

  function onMouseMove(e: MouseEvent) {
    pendingDeltaX += e.screenX - startX
    pendingDeltaY += e.screenY - startY
    startX = e.screenX
    startY = e.screenY
    scheduleResize()
  }
  function onMouseUp() {
    if (frame !== null) cancelAnimationFrame(frame)
    flushResize()
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}
</script>

<template>
  <div class="resize-handles">
    <div class="handle n" @mousedown="handleResizeStart($event, 'n')" />
    <div class="handle s" @mousedown="handleResizeStart($event, 's')" />
    <div class="handle e" @mousedown="handleResizeStart($event, 'e')" />
    <div class="handle w" @mousedown="handleResizeStart($event, 'w')" />
    <div class="handle ne" @mousedown="handleResizeStart($event, 'ne')" />
    <div class="handle nw" @mousedown="handleResizeStart($event, 'nw')" />
    <div class="handle se" @mousedown="handleResizeStart($event, 'se')" />
    <div class="handle sw" @mousedown="handleResizeStart($event, 'sw')" />
  </div>
</template>

<style scoped>
.resize-handles {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
  z-index: 9999;
  border-radius: var(--window-radius, 16px);
  overflow: hidden;
}

.handle {
  position: absolute;
  pointer-events: auto;
}

.handle.n { top: 0; left: 5px; right: 5px; height: 5px; cursor: n-resize; }
.handle.s { bottom: 0; left: 5px; right: 5px; height: 5px; cursor: s-resize; }
.handle.e { top: 5px; bottom: 5px; right: 0; width: 5px; cursor: e-resize; }
.handle.w { top: 5px; bottom: 5px; left: 0; width: 5px; cursor: w-resize; }

.handle.nw { top: 0; left: 0; width: 10px; height: 10px; cursor: nw-resize; border-top-left-radius: var(--window-radius, 16px); }
.handle.ne { top: 0; right: 0; width: 10px; height: 10px; cursor: ne-resize; border-top-right-radius: var(--window-radius, 16px); }
.handle.sw { bottom: 0; left: 0; width: 10px; height: 10px; cursor: sw-resize; border-bottom-left-radius: var(--window-radius, 16px); }
.handle.se { bottom: 0; right: 0; width: 10px; height: 10px; cursor: se-resize; border-bottom-right-radius: var(--window-radius, 16px); }
</style>
