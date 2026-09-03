import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import { copyFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'

const cubismCoreSource = resolve(__dirname, '..', 'dsakiko_webui', 'frontend', 'public', 'cubism', 'core5')
const cubismCoreFiles = [
  'live2dcubismcore.min.js',
  'LICENSE.md',
  'NOTICE.md',
  'RedistributableFiles.txt',
]
const cubism2Source = resolve(__dirname, '..', 'dsakiko_webui', 'frontend', 'public', 'live2d.min.js')

function cubismCore5Assets() {
  return {
    name: 'dsakiko-cubism-core5-assets',
    configureServer(server: any) {
      // Development serves the single upstream WebUI copy. No proprietary
      // Core is duplicated into Electron source or committed there.
      server.middlewares.use('/sdk/core5', (request: any, response: any, next: () => void) => {
        const requested = basename(String(request.url || '').split('?')[0])
        if (!cubismCoreFiles.includes(requested)) return next()
        const source = join(cubismCoreSource, requested)
        if (!existsSync(source)) return next()
        response.setHeader('Cache-Control', 'no-store')
        response.end(readFileSync(source))
      })
    },
    closeBundle() {
      // The packaged renderer needs a local file:// asset. Copy it only to
      // ignored build output together with the upstream license/notice.
      const target = resolve(__dirname, 'dist', 'renderer', 'sdk', 'core5')
      mkdirSync(target, { recursive: true })
      for (const file of cubismCoreFiles) {
        const source = join(cubismCoreSource, file)
        if (!existsSync(source)) throw new Error(`Missing upstream Cubism Core asset: ${source}`)
        copyFileSync(source, join(target, file))
      }
    },
  }
}

function cubism2Asset() {
  return {
    name: 'dsakiko-cubism2-asset',
    configureServer(server: any) {
      server.middlewares.use('/sdk/live2d.min.js', (_request: any, response: any, next: () => void) => {
        if (!existsSync(cubism2Source)) return next()
        response.setHeader('Cache-Control', 'no-store')
        response.end(readFileSync(cubism2Source))
      })
    },
    closeBundle() {
      const target = resolve(__dirname, 'dist', 'renderer', 'sdk', 'live2d.min.js')
      if (!existsSync(cubism2Source)) throw new Error(`Missing upstream Cubism 2 asset: ${cubism2Source}`)
      mkdirSync(resolve(__dirname, 'dist', 'renderer', 'sdk'), { recursive: true })
      copyFileSync(cubism2Source, target)
    },
  }
}

export default defineConfig(async ({ mode }) => {
  const rendererPlugins = [vue(), UnoCSS()]
  if (mode === 'development') {
    // Keep Vue DevTools available to developers without shipping it in production.
    // The import is lazy so production builds do not resolve the dev-only package.
    const { default: VueDevTools } = await import('vite-plugin-vue-devtools')
    rendererPlugins.push(VueDevTools())
  }

  return {
    main: {
      build: {
        outDir: 'dist/main',
        rollupOptions: {
          external: ['electron']
        }
      }
    },
    preload: {
      build: {
        outDir: 'dist/preload',
        rollupOptions: {
          external: ['electron']
        }
      }
    },
    renderer: {
      plugins: [...rendererPlugins, cubismCore5Assets(), cubism2Asset()],
      build: {
        outDir: 'dist/renderer'
      }
    }
  }
})
