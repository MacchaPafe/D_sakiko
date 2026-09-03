import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const stampPath = path.join(root, 'dist', '.dsakiko-runtime-inputs.sha256')
const dependencyStampPath = path.join(root, 'node_modules', '.dsakiko-dependencies.sha256')
const inputs = [
  'package.json',
  'package-lock.json',
  'electron.vite.config.ts',
  'tsconfig.json',
  'uno.config.ts',
  'src',
]

function appendTree(hash, relativePath) {
  const absolutePath = path.join(root, relativePath)
  if (!fs.existsSync(absolutePath)) return
  const stat = fs.statSync(absolutePath)
  if (stat.isDirectory()) {
    for (const name of fs.readdirSync(absolutePath).sort()) appendTree(hash, path.join(relativePath, name))
    return
  }
  hash.update(relativePath.replaceAll(path.sep, '/'))
  hash.update('\0')
  hash.update(fs.readFileSync(absolutePath))
  hash.update('\0')
}

function inputDigest() {
  const hash = createHash('sha256')
  for (const input of inputs) appendTree(hash, input)
  return hash.digest('hex')
}

function dependencyDigest() {
  const hash = createHash('sha256')
  for (const input of ['package.json', 'package-lock.json']) {
    hash.update(input)
    hash.update('\0')
    hash.update(fs.readFileSync(path.join(root, input)))
    hash.update('\0')
  }
  return hash.digest('hex')
}

const digest = inputDigest()
if (process.argv.includes('--dependencies-current')) {
  const expectedDigest = dependencyDigest()
  const installedDigest = fs.existsSync(dependencyStampPath)
    ? fs.readFileSync(dependencyStampPath, 'utf8').trim()
    : ''
  const installedPath = path.join(root, 'node_modules', 'electron', 'package.json')
  const installed = fs.existsSync(installedPath)
    ? JSON.parse(fs.readFileSync(installedPath, 'utf8')).version
    : ''
  const electronRoot = path.join(root, 'node_modules', 'electron')
  const platformPath = process.platform === 'win32'
    ? 'electron.exe'
    : process.platform === 'darwin'
      ? 'Electron.app/Contents/MacOS/Electron'
      : 'electron'
  const pathFile = path.join(electronRoot, 'path.txt')
  const pathValue = fs.existsSync(pathFile) ? fs.readFileSync(pathFile, 'utf8').trim() : ''
  const executable = path.join(electronRoot, 'dist', platformPath)
  const runtimeReady = pathValue === platformPath && fs.existsSync(executable)
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json'), 'utf8'))
  const expectedElectron = lock.packages?.['node_modules/electron']?.version
  process.exit(
    installedDigest === expectedDigest
      && typeof expectedElectron === 'string'
      && expectedElectron === installed
      && runtimeReady
      ? 0
      : 2,
  )
}
if (process.argv.includes('--dependencies-write')) {
  fs.mkdirSync(path.dirname(dependencyStampPath), { recursive: true })
  fs.writeFileSync(dependencyStampPath, `${dependencyDigest()}\n`, 'utf8')
  process.exit(0)
}
if (process.argv.includes('--write')) {
  if (!fs.existsSync(path.join(root, 'dist', 'main', 'index.js'))) {
    throw new Error('cannot stamp a missing Electron production build')
  }
  fs.mkdirSync(path.dirname(stampPath), { recursive: true })
  fs.writeFileSync(stampPath, `${digest}\n`, 'utf8')
  process.exit(0)
}

const existing = fs.existsSync(stampPath) ? fs.readFileSync(stampPath, 'utf8').trim() : ''
const built = fs.existsSync(path.join(root, 'dist', 'main', 'index.js'))
process.exit(built && existing === digest ? 0 : 2)
