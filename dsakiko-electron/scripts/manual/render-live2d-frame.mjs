import { app, BrowserWindow, net, protocol } from 'electron'
import { mkdir, mkdtemp, readdir, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// 在 dsakiko-electron 下运行：pnpm exec electron scripts/manual/render-live2d-frame.mjs [模型目录或 JSON] [输出 PNG] [动作组] [表情名]
const electronRoot = fileURLToPath(new URL('../../', import.meta.url))
const projectRoot = path.dirname(electronRoot)
const defaultModel = path.join(
  projectRoot,
  'live2d_related/anon/extra_model/adv_live2d_anon_002_casual_spring_01'
)
const modelInput = path.resolve(process.argv[2] || defaultModel)
const outputPath = path.resolve(
  process.argv[3] || path.join(projectRoot, '.scratch/live2d-preview/anon-spring.png')
)

/**
 * @typedef {object} FrameOptions
 * @property {string} modelUrl 模型 JSON 地址。
 * @property {string} motionGroup 要截取的动作组，使用第一个动作。
 * @property {string} expression 表情名，空字符串表示保留动作表情。
 */

/**
 * 在隔离的渲染页面中生成透明 PNG，不依赖屏幕分辨率或窗口截图。
 * @param {FrameOptions} options 渲染配置。
 * @returns {Promise<{dataUrl: string, width: number, height: number}>} 裁去透明留白的截图。
 */
async function renderFrame(options) {
  const PIXI = globalThis.PIXI
  const canvasWidth = 3200
  const canvasHeight = 4000
  const pixiApp = new PIXI.Application({
    width: canvasWidth,
    height: canvasHeight,
    resolution: 1,
    backgroundAlpha: 0,
    antialias: true,
    autoStart: false,
    preserveDrawingBuffer: true
  })
  globalThis.document.body.appendChild(pixiApp.view)
  const model = await PIXI.live2d.Live2DModel.from(options.modelUrl, {
    autoUpdate: false,
    autoInteract: false,
    motionPreload: 'NONE',
    idleMotionGroup: options.motionGroup
  })
  pixiApp.stage.addChild(model)
  const scale = Math.min((canvasWidth - 160) / model.width, (canvasHeight - 160) / model.height)
  model.scale.set(scale)
  model.anchor.set(0.5, 0.5)
  model.position.set(canvasWidth / 2, canvasHeight / 2)

  if (!(await model.motion(options.motionGroup, 0, 3))) {
    throw new Error(`无法加载动作组：${options.motionGroup}`)
  }
  if (options.expression && !(await model.expression(options.expression))) {
    throw new Error(`无法加载表情：${options.expression}`)
  }

  // 模型在实际绘制时才更新物理状态；逐帧推进一秒，避免截到初始化姿势。
  for (let frame = 0; frame < 60; frame += 1) {
    model.update(1000 / 60)
    pixiApp.renderer.render(pixiApp.stage)
  }

  const canvas = globalThis.document.createElement('canvas')
  canvas.width = canvasWidth
  canvas.height = canvasHeight
  const context = canvas.getContext('2d', { willReadFrequently: true })
  context.drawImage(pixiApp.view, 0, 0)
  const pixels = context.getImageData(0, 0, canvasWidth, canvasHeight).data
  let left = canvasWidth
  let top = canvasHeight
  let right = -1
  let bottom = -1
  for (let y = 0; y < canvasHeight; y += 1) {
    for (let x = 0; x < canvasWidth; x += 1) {
      if (pixels[(y * canvasWidth + x) * 4 + 3] === 0) continue
      left = Math.min(left, x)
      top = Math.min(top, y)
      right = Math.max(right, x)
      bottom = Math.max(bottom, y)
    }
  }
  if (right < 0) throw new Error('渲染结果完全透明，请检查模型和 WebGL')
  if (left === 0 || top === 0 || right === canvasWidth - 1 || bottom === canvasHeight - 1) {
    throw new Error('角色触及画布边缘，请减小渲染比例后重试，避免保存截断的图片')
  }

  const padding = 24
  const cropped = globalThis.document.createElement('canvas')
  cropped.width = right - left + 1 + padding * 2
  cropped.height = bottom - top + 1 + padding * 2
  cropped.getContext('2d').drawImage(canvas, padding - left, padding - top)
  const result = {
    dataUrl: cropped.toDataURL('image/png'),
    width: cropped.width,
    height: cropped.height
  }
  model.destroy({ texture: true, baseTexture: true })
  pixiApp.destroy(true)
  return result
}

/**
 * 解析单个 Cubism 3/4 模型入口，目录中存在多个入口时拒绝猜测。
 * @param {string} input 模型目录或 JSON 文件。
 * @returns {Promise<string>} 模型入口的绝对路径。
 */
async function resolveModelFile(input) {
  if (!(await stat(input)).isDirectory()) return input
  const entries = (await readdir(input)).filter((name) => name.endsWith('.model3.json'))
  if (entries.length !== 1) throw new Error('请指定唯一的 .model3.json 文件')
  return path.join(input, entries[0])
}

// 自定义本地协议让 JSON、纹理与运行库同源加载，无需启动服务器或关闭浏览器安全检查。
protocol.registerSchemesAsPrivileged([
  { scheme: 'snapshot', privileges: { standard: true, secure: true, supportFetchAPI: true } }
])
const sessionDirectory = await mkdtemp(path.join(tmpdir(), 'dsakiko-live2d-frame-'))
app.setPath('userData', sessionDirectory)
const timeout = setTimeout(() => {
  console.error('Live2D 截图超时（60 秒）')
  app.exit(1)
}, 60_000)

/** 执行截图并清理临时会话；不阻塞 ESM 入口完成加载和 Electron 的 ready 事件。 */
async function main() {
  try {
    const modelFile = await resolveModelFile(modelInput)
    const modelDirectory = path.dirname(modelFile)
    const runtimeFiles = new Map([
      ['/pixi.js', path.join(electronRoot, 'node_modules/pixi.js/dist/browser/pixi.min.js')],
      [
        '/core.js',
        path.join(electronRoot, 'src/renderer/public/cubism/core5/live2dcubismcore.min.js')
      ],
      [
        '/display.js',
        path.join(electronRoot, 'node_modules/pixi-live2d-display/dist/cubism4.min.js')
      ]
    ])
    console.log('等待 Electron 就绪…')
    await app.whenReady()
    console.log('Electron 已就绪，加载本地渲染页面…')
    protocol.handle('snapshot', async function serveLocalAsset(request) {
      const url = new URL(request.url)
      if (url.hostname !== 'local') return new Response('Not found', { status: 404 })
      if (url.pathname === '/') {
        return new Response(
          '<!doctype html><meta charset="utf-8"><style>body{margin:0}</style>' +
            '<script src="/pixi.js"></script><script src="/core.js"></script>' +
            '<script src="/display.js"></script>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        )
      }
      let file = runtimeFiles.get(url.pathname)
      if (!file && url.pathname.startsWith('/model/')) {
        file = path.resolve(
          modelDirectory,
          decodeURIComponent(url.pathname.slice('/model/'.length))
        )
        const relative = path.relative(modelDirectory, file)
        if (relative.startsWith('..') || path.isAbsolute(relative)) {
          return new Response('Forbidden', { status: 403 })
        }
      }
      if (!file) return new Response('Not found', { status: 404 })
      return net.fetch(pathToFileURL(file).href)
    })
    const window = new BrowserWindow({
      show: false,
      width: 800,
      height: 1000,
      webPreferences: { offscreen: true, backgroundThrottling: false, sandbox: true }
    })
    window.webContents.on('console-message', function reportRendererMessage(details) {
      if (details.level === 'error') console.error(details.message)
    })
    await window.loadURL('snapshot://local/')
    console.log('页面已加载，渲染模型…')
    const options = {
      modelUrl: `snapshot://local/model/${encodeURIComponent(path.basename(modelFile))}`,
      motionGroup: process.argv[4] || 'IDLE_C',
      expression: process.argv[5] || ''
    }
    const result = await window.webContents.executeJavaScript(
      `(${renderFrame.toString()})(${JSON.stringify(options)})`
    )
    await mkdir(path.dirname(outputPath), { recursive: true })
    await writeFile(outputPath, Buffer.from(result.dataUrl.split(',')[1], 'base64'))
    console.log(
      JSON.stringify({ output: outputPath, width: result.width, height: result.height }, null, 2)
    )
    window.destroy()
  } catch (error) {
    console.error(error)
    process.exitCode = 1
  } finally {
    clearTimeout(timeout)
    await rm(sessionDirectory, { recursive: true, force: true })
    app.exit(process.exitCode || 0)
  }
}

void main()
