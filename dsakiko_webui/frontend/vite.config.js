import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import {
  cpSync,
  createReadStream,
  existsSync,
  mkdirSync,
  statSync,
} from 'node:fs'
import { extname, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = fileURLToPath(new URL('../..', import.meta.url))
const frontendRoot = fileURLToPath(new URL('.', import.meta.url))
const outputRoot = resolve(frontendRoot, 'dist', 'mock-assets')

const mockAssets = [
  ['live2d/anon', resolve(projectRoot, 'live2d_related/anon/live2D_model')],
  ['live2d/arisa', resolve(projectRoot, 'live2d_related/arisa/live2D_model')],
  ['live2d/sakiko', resolve(projectRoot, 'live2d_related/sakiko/live2D_model')],
  ['avatars/爱音.png', resolve(projectRoot, 'GPT_SoVITS/char_headprof/爱音.png')],
  ['avatars/有咲.png', resolve(projectRoot, 'GPT_SoVITS/char_headprof/有咲.png')],
  ['avatars/祥子.png', resolve(projectRoot, 'live2d_related/sakiko/sakiko_icon.png')],
  ['backgrounds/bg00474.png', resolve(projectRoot, 'live2d_related/bg00474.png')],
  [
    'audio/anon-reference.wav',
    resolve(projectRoot, 'reference_audio/anon/anon_X.wav_0008742720_0008876160.wav'),
  ],
  [
    'audio/sakiko-white.wav',
    resolve(projectRoot, 'reference_audio/sakiko/white_sakiko.wav'),
  ],
  [
    'audio/silence.wav',
    resolve(projectRoot, 'reference_audio/silent_audio/silence.wav'),
  ],
]

const contentTypes = {
  '.json': 'application/json; charset=utf-8',
  '.moc': 'application/octet-stream',
  '.mtn': 'application/octet-stream',
  '.physics': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.wav': 'audio/wav',
}

function resolveMockAsset(requestPath) {
  const decodedPath = decodeURIComponent(requestPath.split('?')[0]).replace(/^\/+/, '')
  const match = mockAssets.find(([publicPath]) => (
    decodedPath === publicPath || decodedPath.startsWith(`${publicPath}/`)
  ))
  if (!match) return null

  const [publicPath, sourcePath] = match
  const sourceStat = statSync(sourcePath)
  const suffix = decodedPath.slice(publicPath.length).replace(/^\/+/, '')
  const resolvedPath = sourceStat.isDirectory() ? resolve(sourcePath, suffix) : sourcePath
  const sourceRoot = sourceStat.isDirectory() ? `${sourcePath}${sep}` : sourcePath
  if (sourceStat.isDirectory() && resolvedPath !== sourcePath && !resolvedPath.startsWith(sourceRoot)) {
    return null
  }
  return resolvedPath
}

function mockProjectAssets() {
  const serveAssets = (server) => {
    server.middlewares.use('/mock-assets', (request, response, next) => {
      try {
        const assetPath = resolveMockAsset(request.url || '')
        if (!assetPath || !existsSync(assetPath) || !statSync(assetPath).isFile()) {
          next()
          return
        }
        response.setHeader(
          'Content-Type',
          contentTypes[extname(assetPath).toLowerCase()] || 'application/octet-stream',
        )
        createReadStream(assetPath).pipe(response)
      } catch {
        next()
      }
    })
  }

  return {
    name: 'mock-project-assets',
    configureServer(server) {
      serveAssets(server)
    },
    configurePreviewServer(server) {
      serveAssets(server)
    },
    writeBundle() {
      mkdirSync(outputRoot, { recursive: true })
      for (const [publicPath, sourcePath] of mockAssets) {
        const targetPath = resolve(outputRoot, publicPath)
        mkdirSync(resolve(targetPath, '..'), { recursive: true })
        cpSync(sourcePath, targetPath, { recursive: true })
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), mockProjectAssets()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    fs: {
      allow: [projectRoot],
    },
  },
})
