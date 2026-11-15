import path from 'node:path'
import { realpath, readdir } from 'node:fs/promises'
import { requireValue } from '../core/support.js'
import { isWithin } from '../resources/files.js'
import { relativeFile, sourcePath, walk } from './files.js'

/**
 * @typedef {object} LegacySourceAccess
 * @property {string} root 已解析的旧版根目录。
 * @property {string[]} excludedRoots 新版数据的排除范围，随扫描持久保存。
 * @property {(name: string, packageRoot?: boolean) => Promise<string>} path 校验单文件或整包后返回实际路径。
 * @property {(name: string) => Promise<import('node:fs').Dirent[]>} list 只列出未受保护的直接子项。
 * @property {(name: string) => Promise<string[]>} walk 递归列出来源文件，跳过新版数据且拒绝符号链接。
 */

/**
 * @param {string} root 用户授权的旧版根目录。
 * @param {string[]} excludedRoots 新版应用数据、资源库和缓存的完整根目录。
 * @param {(name: string) => void} onExcluded 扫描时报告被排除的相对位置，不读取目录内容。
 * @returns {Promise<LegacySourceAccess>} 扫描、复核与最终导入共用的来源访问规则。
 */
export async function createLegacySource(root, excludedRoots = [], onExcluded = () => {}) {
  root = await realpath(root)
  const roots = await Promise.all(
    excludedRoots.map(async (directory) => {
      try {
        return await realpath(directory)
      } catch (error) {
        // 历史扫描中的排除目录即使已被删除，也不能重新成为可导入来源。
        if (error.code === 'ENOENT') return path.resolve(directory)
        throw error
      }
    })
  )
  const exclusions = [...new Set(roots)]
  requireValue(
    !exclusions.some((directory) => isWithin(directory, root)),
    'legacy_directory_overlaps_data',
    '不能选择新版数据目录或其内部文件夹作为旧版来源。'
  )
  function excluded(absolute) {
    if (!exclusions.some((directory) => isWithin(directory, absolute))) return false
    onExcluded(path.relative(root, absolute).split(path.sep).join('/'))
    return true
  }
  async function resolve(name, packageRoot = false) {
    relativeFile(name)
    const absolute = path.resolve(root, name)
    requireValue(!excluded(absolute), 'legacy_new_data_excluded', '资料位于新版数据目录，已跳过。')
    // 整包复制会读取隐藏文件，不能只依赖扫描的逐文件过滤；重叠时跳过整个模型包。
    requireValue(
      !packageRoot || !exclusions.some((directory) => isWithin(absolute, directory)),
      'legacy_package_overlaps_data',
      '模型包内包含新版数据目录，已跳过该模型包。'
    )
    const actual = await realpath(await sourcePath(root, name))
    requireValue(!excluded(actual), 'legacy_new_data_excluded', '资料位于新版数据目录，已跳过。')
    requireValue(
      !packageRoot || !exclusions.some((directory) => isWithin(actual, directory)),
      'legacy_package_overlaps_data',
      '模型包内包含新版数据目录，已跳过该模型包。'
    )
    return actual
  }
  async function list(name) {
    const directory = await resolve(name)
    return (await readdir(directory, { withFileTypes: true })).filter(
      (entry) => !excluded(path.join(directory, entry.name))
    )
  }
  async function files(name) {
    return walk(await resolve(name), '', excluded)
  }
  return { root, excludedRoots: exclusions, path: resolve, list, walk: files }
}
