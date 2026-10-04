import { fileURLToPath } from 'node:url'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {},
  preload: {
    build: {
      // 沙箱 preload 使用单个 CommonJS 文件，不依赖页面中的 Node 环境。
      rollupOptions: {
        output: { format: 'cjs', entryFileNames: 'index.cjs' }
      }
    }
  },
  renderer: {
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src/renderer/src', import.meta.url)) }
    },
    plugins: [react(), tailwindcss()]
  }
})
