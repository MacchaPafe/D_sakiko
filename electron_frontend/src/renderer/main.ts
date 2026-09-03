import 'virtual:uno.css'
import { createApp } from 'vue'
import App from './App.vue'
import './styles/main.css'

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = src
    script.onload = () => resolve()
    script.onerror = () => reject(new Error(`Failed to load ${src}`))
    document.head.appendChild(script)
  })
}

// 默认暗色模式
if (!localStorage.getItem('saki-theme')) {
  localStorage.setItem('saki-theme', 'dark')
}
const theme = localStorage.getItem('saki-theme')
if (theme === 'dark') {
  document.documentElement.classList.add('dark')
} else {
  document.documentElement.classList.add('light')
}

async function bootstrap() {
  // Cubism 3/4 comes from the one licensed upstream WebUI Core 5 source. The
  // electron-vite build copies that file plus its NOTICE/LICENSE into ignored
  // dist output; dev mode serves the same source without a second copy.
  try {
    if (!(window as any).Live2DCubismCore) {
      await loadScript('./sdk/core5/live2dcubismcore.min.js')
    }
    console.log('[SDK] optional Cubism Core loaded')
  } catch (e) {
    console.warn('[SDK] optional Cubism Core not installed; V3 models are unavailable:', e)
  }
  try {
    // Production Electron loads the renderer with file://.  Keep this
    // relative so the packaged dist/renderer/sdk files are found; an
    // absolute /sdk path resolves to the drive root under file://.
    await loadScript('./sdk/live2d.min.js')
    console.log('[SDK] Cubism 2 SDK loaded')
  } catch (e) {
    console.warn('[SDK] Cubism 2 SDK not found:', e)
  }

  const app = createApp(App)

  if (import.meta.env.DEV) {
    const devtoolsHook = (window as any).__VUE_DEVTOOLS_GLOBAL_HOOK__
    if (devtoolsHook) {
      devtoolsHook.emit('init', app)
    }
  }

  app.mount('#app')
}

bootstrap()
