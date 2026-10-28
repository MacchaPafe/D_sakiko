import path from 'node:path'
import { copyFile, chmod, lstat, mkdir, readdir, realpath } from 'node:fs/promises'
import { requireValue } from '../core/support.js'

/** 工作文件只使用跨平台有效的单个文件名，拒绝静默截断用户指定的路径。 */
export function validWorkspaceFilename(name) {
  return (
    typeof name === 'string' &&
    name.length > 0 &&
    Buffer.byteLength(name) <= 180 &&
    !/[\\/<>:"|?*]/u.test(name) &&
    ![...name].some((character) => character.charCodeAt(0) < 32) &&
    !/[. ]$/u.test(name) &&
    name !== '.' &&
    name !== '..' &&
    !/^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(name)
  )
}

/** 资源索引中的工作文件位置必须包含受校验的对话 ID 和文件名。 */
export function validWorkspaceLocation(relative) {
  if (typeof relative !== 'string') return false
  const parts = relative.split('/')
  return (
    parts.length === 3 &&
    parts[0] === 'workspaces' &&
    /^[\p{L}\p{N}_-]{1,100}$/u.test(parts[1]) &&
    validWorkspaceFilename(parts[2])
  )
}

/**
 * 校验资源库 models 内的文件名；用途由引用方决定，不绑定某个引擎的目录布局。
 * @param {unknown} value 相对于 models 的路径。
 * @returns {boolean} 是否为不越界的相对文件路径。
 */
export function validModelFilePath(value) {
  return (
    typeof value === 'string' &&
    value.length <= 1000 &&
    !/[\\:\0]/u.test(value) &&
    value.split('/').every((part) => part && part !== '.' && part !== '..')
  )
}

/**
 * 检查路径是否位于指定目录内。
 * @param {string} root 根目录。
 * @param {string} target 待检查路径。
 * @returns {boolean} 是否位于根目录内。
 */
export function isWithin(root, target) {
  const relative = path.relative(root, target)
  return (
    relative === '' ||
    (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep))
  )
}

/**
 * 解析资源内部的相对路径。
 * @param {string} root 资源根目录。
 * @param {string} relative 以斜杠分隔的相对路径。
 * @returns {string} 解析后的绝对路径。
 */
export function resourcePath(root, relative) {
  requireValue(
    typeof relative === 'string' &&
      relative.length > 0 &&
      !relative.includes('\\') &&
      !relative.includes(':') &&
      !relative.includes('\0'),
    'invalid_asset_path'
  )
  requireValue(
    relative.split('/').every((part) => part && part !== '.' && part !== '..'),
    'invalid_asset_path'
  )
  const target = path.resolve(root, relative)
  requireValue(isWithin(root, target), 'invalid_asset_path')
  return target
}

/**
 * 校验现有文件的实际位置，拒绝指向目录外的符号链接。
 * @param {string} root 根目录。
 * @param {string} target 文件或目录路径。
 * @returns {Promise<string>} 实际路径。
 */
export async function containedPath(root, target) {
  const actualRoot = await realpath(root)
  const actual = await realpath(target)
  requireValue(isWithin(actualRoot, actual), 'invalid_asset_path')
  return actual
}

/**
 * 复制完整模型目录，保留包内结构，不导入符号链接或特殊文件。
 * @param {string} source 源目录。
 * @param {string} target 目标目录。
 * @param {AbortSignal} [signal] 取消信号。
 * @returns {Promise<number>} 复制的总字节数。
 */
export async function copyPackage(source, target, signal) {
  signal?.throwIfAborted()
  const metadata = await lstat(source)
  requireValue(metadata.isDirectory() && !metadata.isSymbolicLink(), 'invalid_asset_package')
  await mkdir(target, { recursive: true })
  let size = 0
  for (const name of await readdir(source)) {
    signal?.throwIfAborted()
    const from = path.join(source, name)
    const to = resourcePath(target, name)
    const item = await lstat(from)
    requireValue(!item.isSymbolicLink(), 'invalid_asset_package')
    if (item.isDirectory()) size += await copyPackage(from, to, signal)
    else {
      requireValue(item.isFile(), 'invalid_asset_package')
      await copyFile(from, to)
      await chmod(to, 0o600)
      size += item.size
    }
  }
  return size
}
