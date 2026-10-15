import path from 'node:path'
import { realpathSync } from 'node:fs'
import { requireValue } from '../backend/core/support.js'
import { isWithin } from '../backend/resources/files.js'

/**
 * @typedef {object} DataDirectories
 * @property {string} appDataRoot 应用数据根目录。
 * @property {string} resourceLibraryRoot 模型与音频资源库根目录。
 * @property {string} cacheRoot 缓存与临时文件根目录。
 */

/** 解析尚未创建的路径，并保留现有父目录的实际位置。 */
function actualPath(target) {
  try {
    return realpathSync(target)
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    const parent = path.dirname(target)
    if (parent === target) throw error
    return path.join(actualPath(parent), path.basename(target))
  }
}

/**
 * 根据系统目录和启动配置确定三个可写根目录。
 * @param {{platform: string, home: string, env: Record<string, string | undefined>}} options 宿主环境。
 * @returns {DataDirectories} 启动期间固定的目录配置。
 */
export function resolveDataDirectories({ platform, home, env }) {
  const paths = platform === 'win32' ? path.win32 : path.posix
  let base
  let cache
  if (platform === 'win32') {
    requireValue(
      env.LOCALAPPDATA && paths.isAbsolute(env.LOCALAPPDATA),
      'missing_local_app_data',
      '无法确定 LocalAppData 目录。'
    )
    base = paths.join(env.LOCALAPPDATA, 'D_Sakiko')
    cache = paths.join(base, 'cache')
  } else if (platform === 'darwin') {
    base = paths.join(home, 'Library', 'Application Support', 'D_Sakiko')
    cache = paths.join(home, 'Library', 'Caches', 'D_Sakiko')
  } else {
    base = paths.join(env.XDG_DATA_HOME || paths.join(home, '.local', 'share'), 'D_Sakiko')
    cache = paths.join(env.XDG_CACHE_HOME || paths.join(home, '.cache'), 'D_Sakiko')
  }
  const roots = {
    appDataRoot: env.DSAKIKO_DATA_DIR || paths.join(base, 'core-v1'),
    resourceLibraryRoot: env.DSAKIKO_RESOURCE_LIBRARY_DIR || paths.join(base, 'library'),
    cacheRoot: env.DSAKIKO_CACHE_DIR || cache
  }
  for (const root of Object.values(roots))
    requireValue(paths.isAbsolute(root), 'invalid_data_root', '数据目录必须是绝对路径。')
  return roots
}

/**
 * 校验目录互不重叠，且不会写入安装包。
 * @param {DataDirectories} directories 可写根目录。
 * @param {string[]} readOnlyRoots 安装包等只读位置。
 * @returns {void}
 */
export function validateDataDirectories(directories, readOnlyRoots) {
  const roots = Object.values(directories).map(actualPath)
  const protectedRoots = readOnlyRoots.map(actualPath)
  for (let index = 0; index < roots.length; index += 1) {
    const root = roots[index]
    requireValue(
      !protectedRoots.some(
        (protectedRoot) => isWithin(protectedRoot, root) || isWithin(root, protectedRoot)
      ),
      'read_only_installation',
      '数据目录不能与安装包重叠。'
    )
    requireValue(
      roots.every(
        (other, otherIndex) =>
          index === otherIndex || (!isWithin(root, other) && !isWithin(other, root))
      ),
      'overlapping_data_roots',
      '三个数据目录不能重叠。'
    )
  }
}
