import { fileURLToPath } from 'node:url'
import { cpSync, mkdirSync, readFileSync } from 'node:fs'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 只把只读下载凭据注入主进程。开发环境和私有发行包沿用 Python 版的配置。
function downloadToken() {
  if (process.env.DSAKIKO_DOWNLOAD_TOKEN) return process.env.DSAKIKO_DOWNLOAD_TOKEN
  try {
    const source = readFileSync(
      new URL('../GPT_SoVITS/live2d_download/hosted/_credentials.py', import.meta.url),
      'utf8'
    )
    return /^DOWNLOAD_TOKEN\s*=\s*(["'])([^"'\r\n]+)\1\s*$/m.exec(source)?.[2] ?? ''
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    return ''
  }
}

export default defineConfig({
  main: {
    define: { 'import.meta.env.DSAKIKO_BUNDLED_DOWNLOAD_TOKEN': JSON.stringify(downloadToken()) },
    build: {
      // 域名后缀表的入口是 JSON，打入主进程可避免外部 ESM 导入丢失 JSON 类型。
      externalizeDeps: { exclude: ['tlds'] },
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
