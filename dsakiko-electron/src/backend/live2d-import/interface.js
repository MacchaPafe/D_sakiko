import path from 'node:path'
import { lstat, mkdir, mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { fingerprint, requireValue, uuid } from '../core/support.js'
import { relativeFile, sha256, sourcePath } from '../setup/files.js'
import { extractArchive } from '../setup/installers.js'

export const modelPackageLimit = 2 * 1024 ** 3
const maxFiles = 10000
const entryPattern = /(?:^|\.)model3?\.json$/iu

function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
}

async function packageFiles(directory, signal) {
  const files = new Map()
  let size = 0
  let count = 0
  async function walk(prefix) {
    for (const item of await readdir(path.join(directory, prefix), { withFileTypes: true })) {
      signal?.throwIfAborted()
      const name = relativeFile(prefix ? prefix + '/' + item.name : item.name)
      requireValue(++count <= maxFiles, 'live2d_package_too_large', '模型文件夹内的文件过多。')
      requireValue(
        item.isDirectory() || item.isFile(),
        'invalid_live2d_file',
        '模型文件夹只能包含普通文件和目录。'
      )
      if (item.isDirectory()) {
        await walk(name)
        continue
      }
      size += (await lstat(await sourcePath(directory, name))).size
      requireValue(size <= modelPackageLimit, 'live2d_package_too_large', '模型文件夹超过 2 GiB。')
      const canonical = name.normalize('NFC').toLowerCase()
      requireValue(
        !files.has(canonical),
        'duplicate_live2d_file',
        '模型文件夹包含文件名仅大小写或 Unicode 表示不同的文件。'
      )
      files.set(canonical, name)
    }
  }
  requireValue(
    (await lstat(directory)).isDirectory(),
    'invalid_live2d_folder',
    '请选择模型文件夹。'
  )
  await walk('')
  return [...files.values()]
}

function modelReferences(data, v3) {
  const references = v3 ? data.FileReferences : data
  const model = v3 ? references?.Moc : references.model
  const textures = v3 ? references?.Textures : references.textures
  requireValue(
    typeof model === 'string' &&
      /\.moc3?$/iu.test(model) &&
      Array.isArray(textures) &&
      textures.length > 0 &&
      textures.every((file) => typeof file === 'string'),
    'invalid_live2d_model',
    '模型入口缺少有效的模型文件或贴图引用。'
  )
  requireValue(
    model.toLowerCase().endsWith(v3 ? '.moc3' : '.moc'),
    'invalid_live2d_model',
    '入口格式和模型文件的版本不一致。'
  )
  const optional = v3 ? ['Physics', 'Pose', 'DisplayInfo', 'UserData'] : ['physics', 'pose']
  const direct = optional.flatMap((key) => (references[key] ? [references[key]] : []))
  const expressions = (v3 ? references.Expressions : references.expressions) ?? []
  const motions = (v3 ? references.Motions : references.motions) ?? {}
  const indexedMotions = v3 ? (data.DSakiko?.MotionAssets ?? []) : []
  requireValue(
    Array.isArray(expressions) &&
      Array.isArray(indexedMotions) &&
      object(motions) &&
      Object.values(motions).every(Array.isArray),
    'invalid_live2d_model',
    '模型入口的动作或表情列表格式错误。'
  )
  const fileKey = v3 ? 'File' : 'file'
  const soundKey = v3 ? 'Sound' : 'sound'
  const entries = [...expressions, ...Object.values(motions).flat(), ...indexedMotions]
  const extras = entries.flatMap((item) => [
    item?.[fileKey],
    ...(item?.[soundKey] ? [item[soundKey]] : [])
  ])
  return [model, ...textures, ...direct, ...extras]
}

/**
 * 校验唯一模型入口和运行时文件引用，保留包内相对路径。
 * @param {string} directory 用户选定目录或已隔离的解包目录。
 * @param {AbortSignal} [signal] 导入取消信号。
 * @param {string} [knownEntry] 已登记的模型入口，用于比较兼容旧包的模型内容。
 * @returns {Promise<{entry: string, version: 'v2'|'v3', thumbnail: string|undefined, digest: string}>} 已校验的入口及内容身份。
 */
export async function inspectModelFolder(directory, signal, knownEntry) {
  const files = await packageFiles(directory, signal)
  const entries = files.filter(
    (name) =>
      entryPattern.test(path.posix.basename(name)) &&
      (knownEntry === undefined || name === knownEntry)
  )
  requireValue(
    entries.length > 0,
    'live2d_entry_missing',
    '文件夹内没有 model.json 或 model3.json 模型入口。'
  )
  requireValue(
    entries.length === 1,
    'live2d_multiple_entries',
    '请选择只有一个模型入口的文件夹。检测到：' + entries.slice(0, 10).join('、')
  )
  const [entry] = entries
  const filename = await sourcePath(directory, entry)
  requireValue(
    (await lstat(filename)).size <= 8 * 1024 * 1024,
    'invalid_live2d_model',
    '模型入口文件过大。'
  )
  const raw = await readFile(filename, { encoding: 'utf8', signal })
  let data
  try {
    data = JSON.parse(raw.replace(/^\uFEFF/u, ''))
  } catch {
    requireValue(false, 'invalid_live2d_model', '模型入口不是有效的 JSON：' + entry)
  }
  requireValue(object(data), 'invalid_live2d_model', '模型入口应为 JSON 对象。')
  const v3 = /model3\.json$/iu.test(entry)
  const directoryName = path.posix.dirname(entry)
  const hash = createHash('sha256').update(fingerprint(data))
  const references = [...new Set(modelReferences(data, v3))]
  for (const name of references.sort()) {
    signal?.throwIfAborted()
    requireValue(
      typeof name === 'string' &&
        name.length > 0 &&
        !path.posix.isAbsolute(name) &&
        !/[\\:#?%\0]/u.test(name),
      'invalid_live2d_reference',
      '模型包含无效的文件引用。'
    )
    // 原始 V3 包可能从子文件夹引用同包资源；归一化后仍必须是包内路径。
    const member = relativeFile(path.posix.join(directoryName, name))
    requireValue(
      files.includes(member),
      'missing_live2d_reference',
      '模型缺少引用的文件：' + member
    )
    hash.update(JSON.stringify([name, await sha256(await sourcePath(directory, member), signal)]))
  }
  for (const member of [
    entry.replace(/\.json$/iu, '.performance.json'),
    path.posix.join(directoryName, 'performance.shared.json')
  ])
    if (files.includes(member))
      hash.update(await sha256(await sourcePath(directory, member), signal))
  const thumbnail = ['icon.png', 'preview.png', 'thumbnail.png']
    .map((name) => path.posix.join(directoryName, name))
    .find((name) => files.includes(name))
  return { entry, version: v3 ? 'v3' : 'v2', thumbnail, digest: hash.digest('hex') }
}

/** 将本地模型和在线包送入同一条目录校验与资源入库流程。 */
export class Live2dImporter {
  constructor({ assets, cacheRoot }) {
    this.assets = assets
    this.cache = path.join(cacheRoot, 'live2d-staging')
  }

  /**
   * @param {string} directory 完整模型文件夹。
   * @param {string} owner 安装完成前的资源持有者。
   * @param {{name?: string, source?: import('../../shared/contracts/characters.js').CharacterModel['source'], version?: 'v2'|'v3'}} options 展示名称和来源。
   * @param {AbortSignal} [signal] 取消信号。
   * @returns {Promise<import('../../shared/contracts/characters.js').CharacterModel>} 可保存到角色形态的模型条目。
   */
  async importDirectory(directory, owner, options, signal) {
    const info = await inspectModelFolder(directory, signal)
    requireValue(
      options.version === undefined || options.version === info.version,
      'unexpected_live2d_version',
      '模型包的入口版本与下载目录不符。'
    )
    const asset = await this.assets.importAsset(
      { kind: 'local-directory', path: directory, entry: info.entry },
      owner,
      'live2d',
      signal
    )
    const name = (options.name ?? path.basename(directory)).trim().slice(0, 120)
    return {
      id: uuid(),
      name: name || 'Live2D 模型',
      presentation: { model: asset.asset },
      source: options.source ?? { kind: 'local', id: info.digest },
      ...(info.thumbnail && { thumbnail: info.thumbnail })
    }
  }

  /**
   * @param {string} archive 已下载并验证哈希的 ZIP 文件。
   * @param {string} owner 安装完成前的资源持有者。
   * @param {{name: string, source: import('../../shared/contracts/characters.js').CharacterModel['source']}} options 展示名和在线服装身份。
   * @param {AbortSignal} [signal] 取消信号。
   * @returns {Promise<import('../../shared/contracts/characters.js').CharacterModel>} 模型条目。
   */
  async importPackage(archive, owner, options, signal) {
    await mkdir(this.cache, { recursive: true })
    const directory = await mkdtemp(path.join(this.cache, 'zip-'))
    try {
      await extractArchive(
        archive,
        directory,
        'zip',
        modelPackageLimit,
        signal,
        (name) => !name.split('/').some((part) => part === '__MACOSX' || part === '.DS_Store')
      )
      return await this.importDirectory(directory, owner, { ...options, version: 'v3' }, signal)
    } finally {
      await rm(directory, { recursive: true, force: true })
    }
  }
}
