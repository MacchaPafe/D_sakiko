import { fileURLToPath } from 'node:url'
import { cpSync, mkdirSync } from 'node:fs'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {
    build: {
      rollupOptions: {
        input: {
          index: fileURLToPath(new URL('./src/main/index.js', import.meta.url)),
          'recognition-worker': fileURLToPath(
            new URL('./src/backend/recognition/worker.js', import.meta.url)
          )
        },
        output: { entryFileNames: '[name].js' }
      }
    },
    plugins: [
      {
        name: 'fixed-prompts',
        closeBundle() {
          const destination = fileURLToPath(new URL('./out/main/prompts', import.meta.url))
          mkdirSync(destination, { recursive: true })
          cpSync(
            fileURLToPath(new URL('./src/backend/llm/prompts/templates', import.meta.url)),
            destination,
            { recursive: true }
          )
        }
      }
    ]
  },
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
