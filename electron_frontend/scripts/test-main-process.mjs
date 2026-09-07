import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const mainProcess = fs.readFileSync(path.join(root, 'src/main/index.ts'), 'utf8')

assert.match(mainProcess, /\bTray\b/, 'the main process must use Electron Tray directly')
assert.match(mainProcess, /let appTray: Tray \| null = null/,
  'the tray must be held for the Electron process lifetime')
assert.match(mainProcess, /if \(process\.platform !== 'win32' \|\| appTray\) return/,
  'tray creation must stay Windows-only and idempotent')
assert.match(mainProcess, /function trayMenuTemplate\(\): MenuItemConstructorOptions\[\]/,
  'tray menu structure must remain independently extensible')
assert.match(mainProcess, /label: mainWindowIsShown\(\) \? '隐藏窗口' : '显示窗口'/,
  'the menu must describe the current visibility action')
assert.match(mainProcess, /handleTrayCommand\('toggle-window'\)/,
  'show and hide must use a named tray command')
assert.match(mainProcess, /handleTrayCommand\('exit-electron'\)/,
  'exit must use a named tray command')
assert.match(mainProcess, /case 'exit-electron':[\s\S]*mainWindow\.destroy\(\)[\s\S]*destroyTray\(\)[\s\S]*app\.quit\(\)/,
  'explicit Electron exit must destroy the window and tray before quitting')
assert.match(mainProcess, /app\.on\('will-quit', destroyTray\)/,
  'the tray must also be released during regular Electron shutdown')
assert.match(mainProcess, /app\.whenReady\(\)\.then\(\(\) => \{[\s\S]*createTray\(\)/,
  'the tray must be created after Electron is ready')
assert.doesNotMatch(mainProcess, /electron_bridge|main2\.py|kill.*backend/i,
  'tray lifecycle must not own the Qt backend')
assert.match(mainProcess, /ipcMain\.handle\('get-window-state'/,
  'the renderer must obtain authoritative BrowserWindow state from the main process')
assert.match(mainProcess, /alwaysOnTop: mainWindow\.isAlwaysOnTop\(\)/,
  'window state IPC must expose the authoritative always-on-top value')
assert.match(mainProcess, /app\.getPath\('userData'\)[\s\S]*window-preferences\.json/,
  'window preferences must use Electron userData rather than backend configuration')
assert.match(mainProcess, /screen\.getAllDisplays\(\)[\s\S]*screen\.getPrimaryDisplay\(\)/,
  'restored bounds must be validated against current displays')
assert.match(mainProcess, /mainWindow\.on\('hide',[\s\S]*stopWindowStatePolling\(\)/,
  'hide must stop high-frequency cursor polling')
assert.match(mainProcess, /mainWindow\.on\('minimize',[\s\S]*stopWindowStatePolling\(\)/,
  'minimize must stop high-frequency cursor polling')
assert.match(mainProcess, /mainWindow\.on\('show',[\s\S]*startWindowStatePolling\(\)/,
  'show must restore high-frequency cursor polling')
assert.match(mainProcess, /publishWindowState\(true\)[\s\S]*windowStateTimer = setInterval\(publishWindowState, 16\)/,
  'restoring polling must immediately publish a current state sample')
assert.match(mainProcess, /mainWindow\.on\('close',[\s\S]*persistWindowPreferences\(\)/,
  'pending debounced window preferences must flush before the window is destroyed')

console.log('Electron main-process tray checks passed.')
