import path from 'node:path'
import { lstat, readdir, rm, statfs } from 'node:fs/promises'
import { problem, readJson, requireValue, SerialQueue } from '../backend/core/support.js'
import { processUsesResources } from '../backend/resources/process.js'
import { sourcePath } from '../backend/setup/files.js'
import { diskFiles, reclaimableFileBytes, resourceUsage } from '../backend/storage/disk-usage.js'
import { extractAssetReferences } from '../backend/resources/references.js'
import {
  backgroundPreviewCacheDirectory,
  isBackgroundPreviewCacheFile
} from '../backend/backgrounds/preview-cache.js'

const UNRECOGNIZED_GROUP_NAME = '未识别文件'
const UNRECOGNIZED_GROUP_MIN_BYTES = 1024 * 1024
const UNRECOGNIZED_DETAIL_MIN_BYTES = 10 * 1024 * 1024

function isPathInside(pathApi, parent, child) {
  const relative = pathApi.relative(parent, child)
  return relative === '' || (!relative.startsWith('..') && !pathApi.isAbsolute(relative))
}

/**
 * 在打包版本中解析当前应用程序本体的安装路径（目录或 AppImage 文件）。
 * 开发模式下返回 null，避免把 node_modules 或源码仓库计入应用体积。
 * @param {{isPackaged: boolean, platform: string, execPath: string, resourcesPath?: string, env?: Record<string, string | undefined>}} options 运行时环境信息。
 * @returns {string | null} 应用程序本体路径；无法安全确定时返回 null。
 */
export function resolveApplicationBundlePath({
  isPackaged,
  platform,
  execPath,
  resourcesPath,
  env = {}
}) {
  if (!isPackaged || !execPath) return null
  const pathApi = platform === 'win32' ? path.win32 : path.posix
  if (platform === 'darwin') {
    let cursor = pathApi.dirname(execPath)
    while (cursor && cursor !== pathApi.dirname(cursor)) {
      if (cursor.endsWith('.app')) {
        if (!resourcesPath || isPathInside(pathApi, cursor, resourcesPath)) return cursor
        return null
      }
      cursor = pathApi.dirname(cursor)
    }
    return null
  }
  if (platform === 'linux' && env.APPIMAGE) {
    return env.APPIMAGE
  }
  const installDir = pathApi.dirname(execPath)
  if (resourcesPath && !isPathInside(pathApi, installDir, resourcesPath)) return null
  return installDir
}

/** 忽略操作系统自动生成的目录元文件，避免干扰存储明细展示。 */
function isIgnoredSystemFile(parts) {
  const base = parts[parts.length - 1]
  return (
    base === '.DS_Store' || base === 'Thumbs.db' || base === 'desktop.ini' || base.startsWith('._')
  )
}

async function measureVolume(directory) {
  try {
    const [disk, info] = await Promise.all([statfs(directory), lstat(directory)])
    const totalBytes = disk.blocks * disk.bsize
    const availableBytes = disk.bavail * disk.bsize
    if (!Number.isFinite(totalBytes) || totalBytes <= 0) return null
    return { dev: info.dev, totalBytes, availableBytes: Math.max(0, availableBytes) }
  } catch {
    return null
  }
}

const names = {
  appDataRoot: '应用数据',
  resourceLibraryRoot: '资源库',
  cacheRoot: '缓存'
}
const cacheNames = {
  setup: '资源安装下载',
  'outfit-downloads': '服装下载',
  'live2d-staging': 'Live2D 解包暂存',
  'builtin-characters': '内置角色预览',
  'model-catalog': '模型目录缓存',
  'live2d-motion-groups': 'Live2D 动作索引',
  assets: '临时附件和归档',
  'speech-temp': '语音临时文件',
  logs: '语音日志'
}
const backgroundPreviewCacheName = '在线图库缩略图'
const chromiumNames = {
  Cache: 'HTTP 缓存',
  'Code Cache': '代码缓存',
  GPUCache: 'GPU 缓存',
  DawnWebGPUCache: 'GPU 缓存',
  DawnGraphiteCache: 'GPU 缓存',
  GraphiteDawnCache: 'GPU 缓存',
  GPUPersistentCache: 'GPU 缓存'
}
const uuidPattern = /^[a-f0-9-]{36}$/
const terminal = new Set(['cancelled', 'succeeded'])

function appDataName(top, asset) {
  if (asset === 'avatar') return '角色与头像'
  if (asset === 'chat-image' || asset === 'attachment' || asset === 'workspace') return '图片与附件'
  if (top === 'chats') return '聊天存档'
  if (['characters.json', 'personas'].includes(top)) return '角色与头像'
  if (['setup', 'outfit-jobs.json', 'remote-files'].includes(top)) return '任务与远端文件记录'
  if (
    top === 'assets' ||
    /^(settings|ui-preferences|backgrounds|onboarding|notices|chatgpt-accounts|reasoning-models)\.json$/.test(
      top
    )
  )
    return '设置与索引'
  return null
}

function libraryName(top, parts, asset, modelOwners) {
  if (top === 'runtimes' && parts[2] === 'gsv.runtime') return 'GPT-SoVITS 语音合成'
  if (top === 'runtimes' && parts[2] === 'asr.runtime') return '语音识别'
  if (top === 'models' && modelOwners?.has('gsv')) return 'GPT-SoVITS 语音合成'
  if (top === 'models' && modelOwners?.has('asr')) return '语音识别'
  if (top === 'models' && modelOwners?.has('voice')) return '角色语音模型'
  if (top === 'models') return '其他模型'
  if (top === 'live2d') return 'Live2D 模型包'
  if (top === 'audio' && modelOwners?.has('chat')) return '历史回复音频及聊天录音'
  if (top === 'audio') return '角色参考音频与其他音频'
  if (top === '.asset-gc') return '待回收资源'
  return asset?.name ?? null
}

async function measure(root, classify) {
  const groups = new Map()
  const counted = new Set()
  let totalBytes = 0
  for await (const { file, info } of diskFiles(root)) {
    const identity = info.ino && info.dev + ':' + info.ino
    if (identity && counted.has(identity)) continue
    if (identity) counted.add(identity)
    const parts = path.relative(root, file).split(path.sep)
    if (isIgnoredSystemFile(parts)) continue
    totalBytes += info.size
    const { name, detail } = classify(parts)
    if (!groups.has(name)) groups.set(name, { name, bytes: 0, details: new Map() })
    const group = groups.get(name)
    group.bytes += info.size
    if (detail) group.details.set(detail, (group.details.get(detail) ?? 0) + info.size)
  }
  const entries = []
  for (const { details, ...group } of groups.values()) {
    const sortedDetails = [...details]
      .map(([name, bytes]) => ({ name, bytes }))
      .sort((a, b) => b.bytes - a.bytes)
    if (group.name === UNRECOGNIZED_GROUP_NAME) {
      const visibleDetails = sortedDetails.filter(
        (item) => item.bytes >= UNRECOGNIZED_DETAIL_MIN_BYTES
      )
      const hiddenDetails = sortedDetails.filter(
        (item) => item.bytes < UNRECOGNIZED_DETAIL_MIN_BYTES
      )
      if (group.bytes < UNRECOGNIZED_GROUP_MIN_BYTES && !visibleDetails.length) continue
      entries.push({
        ...group,
        details: visibleDetails,
        hiddenCount: hiddenDetails.length,
        hiddenBytes: hiddenDetails.reduce((sum, item) => sum + item.bytes, 0)
      })
      continue
    }
    entries.push({ ...group, details: sortedDetails })
  }
  entries.sort((a, b) => b.bytes - a.bytes)
  return { entries, bytes: totalBytes }
}

function assetOwners(index, installed) {
  const owners = new Map()
  function tag(ids, kind) {
    for (const id of ids) {
      if (!owners.has(id)) owners.set(id, new Set())
      owners.get(id).add(kind)
    }
  }
  for (const kind of ['gsv', 'asr'])
    tag(
      extractAssetReferences(installed[kind + '.models']?.refs).map(({ id }) => id),
      kind
    )
  for (const [owner, ids] of Object.entries(index.owners)) {
    if (owner.startsWith('chat:')) tag(ids, 'chat')
    if (owner.startsWith('character:') || owner.startsWith('installation:voice.')) tag(ids, 'voice')
  }
  return owners
}

const cleanupNames = {
  http: 'HTTP 缓存',
  code: '代码缓存',
  indexes: '可重建的模型目录和动作索引',
  previews: '内置角色预览缓存',
  [backgroundPreviewCacheDirectory]: backgroundPreviewCacheName,
  downloads: '没有任务使用的安装下载文件',
  outfits: '没有任务使用的服装下载与解包文件',
  speech: '已退出的语音运行暂存',
  logs: '旧的语音日志',
  assets: '未使用的资源'
}

/** 仅对当前宿主配置的三个目录做统计和预定义类型的清理，打包版本额外统计只读程序本体。 */
export class StorageSpace {
  constructor({ directories, backend, session, applicationBundle = null }) {
    Object.assign(this, { directories, backend, session, applicationBundle })
    this.cache = directories.cacheRoot
    this.queue = new SerialQueue()
    this.bundleCache = null
  }

  /**
   * 统计打包后应用程序本体的磁盘占用。安装包在运行期间只读不变，首次测量后缓存在内存中。
   * @returns {Promise<object | null>} 应用程序卡片数据；开发模式或读取失败时返回 null。
   */
  async measureApplicationBundle() {
    if (!this.applicationBundle) return null
    if (this.bundleCache) return this.bundleCache
    try {
      let bytes = 0
      const counted = new Set()
      for await (const { info } of diskFiles(this.applicationBundle)) {
        const identity = info.ino && info.dev + ':' + info.ino
        if (identity && counted.has(identity)) continue
        if (identity) counted.add(identity)
        bytes += info.size
      }
      const volume = await measureVolume(this.applicationBundle)
      this.bundleCache = {
        id: 'applicationBundle',
        name: '应用程序',
        directory: this.applicationBundle,
        readonly: true,
        volume,
        bytes,
        entries: [{ name: '程序本体', bytes, details: [] }]
      }
      return this.bundleCache
    } catch {
      return null
    }
  }

  /** @returns {Promise<object[]>} 各根目录和分组占用，个别根目录离线时保留其余结果。 */
  async overview() {
    const { assets, setup } = this.backend
    const [index, installed] = await Promise.all([assets.state(), setup.installations()])
    const indexed = new Map(
      Object.entries(index.locations).map(([id, location]) => [
        location.root + ':' + location.relativePath,
        index.assets[id]
      ])
    )
    const owners = assetOwners(index, installed)
    function classify(key, parts) {
      const [top, second] = parts
      if (key === 'cacheRoot') {
        if (
          top === backgroundPreviewCacheDirectory &&
          parts.length === 2 &&
          isBackgroundPreviewCacheFile(second)
        )
          return { name: backgroundPreviewCacheName }
        const name =
          top === 'chromium' ? (chromiumNames[second] ?? '其他 Chromium 会话文件') : cacheNames[top]
        return {
          name: name ?? '未识别文件',
          detail: top === 'chromium' ? second : name ? null : top
        }
      }
      const relative = parts.join('/')
      if (key === 'appDataRoot') {
        const asset = indexed.get('application:' + parts.slice(1).join('/'))
        const name = appDataName(top, asset?.purpose)
        return { name: name ?? '未识别文件', detail: name ? null : top }
      }
      const asset =
        indexed.get('library:' + relative) ?? indexed.get('library:' + top + '/' + second)
      const assetOwnerSet = owners.get(asset?.asset.id)
      const name = libraryName(top, parts, asset, assetOwnerSet)
      let detail = null
      if (name === 'GPT-SoVITS 语音合成' || name === '语音识别') {
        detail = top === 'runtimes' ? '运行环境' : '基础模型'
      } else if (['models', 'live2d'].includes(top)) {
        detail = asset?.name ?? second
      }
      return { name: name ?? '未识别文件', detail: name ? detail : top }
    }
    const roots = await Promise.all(
      Object.entries(names).map(async ([id, name]) => {
        const directory = this.directories[id]
        try {
          const [usage, volume] = await Promise.all([
            measure(directory, (parts) => classify(id, parts)),
            measureVolume(directory)
          ])
          return {
            id,
            name,
            directory,
            volume,
            ...usage
          }
        } catch (error) {
          return {
            id,
            name,
            directory,
            volume: null,
            bytes: null,
            entries: [],
            problem: problem(error)
          }
        }
      })
    )
    const bundle = await this.measureApplicationBundle()
    if (bundle) roots.push(bundle)
    return roots
  }

  /**
   * 统计每个角色的关联空间占用及基础配置摘要。
   * @returns {Promise<Record<string, {totalBytes: number, live2dBytes: number, voiceBytes: number, otherBytes: number, formCount: number, outfitCount: number, hasVoice: boolean, chatCount: number}>>} 按角色 ID 索引的占用详情。
   */
  async characterUsage() {
    const { characters, assets, conversations } = this.backend
    const [characterState, index, chats] = await Promise.all([
      characters.state(),
      assets.state(),
      conversations.listChats()
    ])
    const chatCounts = new Map()
    for (const chat of chats) {
      for (const characterId of chat.characterIds ?? []) {
        chatCounts.set(characterId, (chatCounts.get(characterId) ?? 0) + 1)
      }
    }
    const assetSizeCache = new Map()
    async function measureAssetBytes(assetId) {
      if (!assetSizeCache.has(assetId)) {
        const pending = assets.collector
          .paths(index, assetId)
          .then(async ({ source }) => {
            let total = 0
            const counted = new Set()
            for await (const { info } of diskFiles(source)) {
              const identity = info.ino && info.dev + ':' + info.ino
              if (identity && counted.has(identity)) continue
              if (identity) counted.add(identity)
              total += info.size
            }
            return total
          })
          .catch(() => 0)
        assetSizeCache.set(assetId, pending)
      }
      return assetSizeCache.get(assetId)
    }

    const result = {}
    for (const id of characterState.order) {
      const role = characterState.characters[id]
      if (!role) continue
      const assetIds = [...new Set(index.owners['character:' + id] ?? [])]
      let live2dBytes = 0
      let voiceBytes = 0
      let otherBytes = 0
      for (const assetId of assetIds) {
        const bytes = await measureAssetBytes(assetId)
        const location = index.locations[assetId]
        const relative = location?.relativePath ?? ''
        const purpose = index.assets[assetId]?.purpose
        if (purpose === 'live2d-package' || relative.startsWith('live2d/')) live2dBytes += bytes
        else if (relative.startsWith('models/') || relative.startsWith('audio/'))
          voiceBytes += bytes
        else otherBytes += bytes
      }
      const outfitIds = new Set()
      for (const form of role.forms ?? []) {
        for (const model of form.models ?? []) outfitIds.add(model.id)
      }
      result[id] = {
        totalBytes: live2dBytes + voiceBytes + otherBytes,
        live2dBytes,
        voiceBytes,
        otherBytes,
        formCount: role.forms?.length ?? 0,
        outfitCount: outfitIds.size,
        hasVoice: Boolean(role.forms?.some((form) => Boolean(form.voice))),
        chatCount: chatCounts.get(id) ?? 0
      }
    }
    return result
  }

  /** 空目录和缺失目录无需清理，内部符号链接不跟随。 */
  async children(relative) {
    try {
      const directory = await sourcePath(this.cache, relative)
      return (await readdir(directory, { withFileTypes: true })).map((entry) => ({
        relative: relative + '/' + entry.name,
        entry
      }))
    } catch (error) {
      if (error.code === 'ENOENT') return []
      throw error
    }
  }

  async fileAction(relativePaths, remove) {
    let bytes = 0
    const failed = []
    for (const relative of relativePaths) {
      try {
        const file = await sourcePath(this.cache, relative)
        bytes += await reclaimableFileBytes(file)
        if (remove) await rm(file, { recursive: true, force: true })
      } catch (error) {
        if (error.code === 'ENOENT') continue
        failed.push(problem(error))
      }
    }
    return { bytes, failed }
  }

  async idleSpeech() {
    const ready = []
    for (const { relative, entry } of await this.children('speech-temp')) {
      if (!entry.isDirectory() || !/^run-[a-f0-9]{32}$/.test(entry.name)) continue
      try {
        const marker = await sourcePath(this.cache, relative + '/.owner.json')
        if (!(await lstat(marker)).isFile()) continue
        const owner = await readJson(marker)
        const { hostPid, pythonPid, processGroupId } = owner ?? {}
        if (owner?.format !== 'dsakiko-speech-run-v1' || owner.runId !== entry.name) continue
        if (
          ![hostPid, pythonPid].every(
            (pid) => Number.isInteger(pid) && pid > 0 && pid <= 0x7fffffff
          )
        )
          continue
        if (processGroupId != null && processGroupId !== pythonPid) continue
        if (
          processUsesResources({ processId: hostPid }) ||
          processUsesResources({ processId: pythonPid })
        )
          continue
        if (
          process.platform !== 'win32' &&
          processGroupId &&
          processUsesResources({
            processId: pythonPid,
            processGroupId
          })
        )
          continue
        ready.push(relative)
      } catch {
        // 缺少有效标记时无法确认进程已经退出。
      }
    }
    return ready
  }

  async action(id, remove) {
    switch (id) {
      case 'http': {
        const bytes = await this.session.getCacheSize()
        if (remove) await this.session.clearCache()
        return { bytes, failed: [] }
      }
      case 'code': {
        const bytes = await this.fileAction(['chromium/Code Cache'], false)
        if (remove) await this.session.clearCodeCaches({})
        return bytes
      }
      case 'indexes': {
        const entries = await this.children('live2d-motion-groups/v1')
        const indexes = entries.filter(
          ({ entry }) => entry.isFile() && /^[a-f0-9]{64}\.json$/.test(entry.name)
        )
        return this.fileAction(
          ['model-catalog/models-dev.json', ...indexes.map((item) => item.relative)],
          remove
        )
      }
      case 'previews': {
        const result = await this.backend.onboarding.builtinResources.withIdleCache(async () => {
          const versions = await this.children('builtin-characters')
          return this.fileAction(
            versions
              .filter(
                ({ entry }) => entry.isDirectory() && /^[\p{L}\p{N}_-]{1,100}$/u.test(entry.name)
              )
              .map(({ relative }) => relative),
            remove
          )
        })
        return result ?? { bytes: 0, failed: [], unavailable: '内置角色预览正在使用。' }
      }
      case backgroundPreviewCacheDirectory:
        return this.backend.backgrounds.withPreviewCacheFiles(remove, (names) =>
          this.fileAction(
            names.map((name) => backgroundPreviewCacheDirectory + '/' + name),
            remove
          )
        )
      case 'downloads':
        return this.backend.setup.withUnusedDownloads((files) =>
          this.fileAction(
            files.map((file) => 'setup/' + path.basename(file)),
            remove
          )
        )
      case 'outfits': {
        const { outfits } = this.backend
        const downloads = (await this.children('outfit-downloads')).filter(({ entry }) => {
          const job = outfits.jobs.get(entry.name)
          return (
            entry.isDirectory() &&
            uuidPattern.test(entry.name) &&
            !outfits.tasks.has(entry.name) &&
            (!job || terminal.has(job.status))
          )
        })
        const unpacked = await this.children('live2d-staging')
        // 解包目录从创建到写入归属有一个间隙，安装任务未退出时保留全部解包目录。
        const staging = outfits.tasks.size
          ? []
          : unpacked.filter(
              ({ entry }) => entry.isDirectory() && /^zip-[a-zA-Z0-9]{6}$/.test(entry.name)
            )
        return this.fileAction(
          downloads.concat(staging).map((item) => item.relative),
          remove
        )
      }
      case 'speech':
        return this.fileAction(await this.idleSpeech(), remove)
      case 'logs': {
        const logs = (await this.children('logs')).filter(
          ({ entry }) => entry.isFile() && /^speech\.log\.[a-zA-Z0-9-]+$/.test(entry.name)
        )
        return this.fileAction(
          logs.map((item) => item.relative),
          remove
        )
      }
      case 'assets': {
        const assets = this.backend.assets
        const preview = await assets.collectUnused({ dryRun: true })
        const candidates = preview.candidates.map(({ asset }) => asset.id)
        const bytes = await resourceUsage(assets, await assets.state()).estimate(candidates)
        const failed = preview.failed.map(({ problem }) => problem)
        if (remove) {
          const result = await assets.collectUnused()
          failed.push(...result.failed.map(({ problem }) => problem))
          const previewed = new Set(candidates)
          const skipped = result.skipped.filter(({ asset }) => previewed.has(asset.id)).length
          return { bytes, failed, skipped, missing: result.missing.length }
        }
        return { bytes, failed, count: candidates.length + preview.orphaned.length }
      }
      default:
        requireValue(false, 'invalid_cleanup')
    }
  }

  /** @returns {Promise<object[]>} 当前可清理项目和预览错误。 */
  async preview() {
    return this.run(Object.keys(cleanupNames), false)
  }

  /** @param {string[]} ids 经用户勾选的清理类型。 @returns {Promise<object[]>} 单项清理结果。 */
  async clean(ids) {
    requireValue(Array.isArray(ids) && ids.every((id) => Object.hasOwn(cleanupNames, id)))
    return this.run([...new Set(ids)], true)
  }

  run(ids, remove) {
    return this.queue.run('storage', async () => {
      const rows = []
      for (const id of ids) {
        const row = { id, name: cleanupNames[id], selected: id !== 'logs' && id !== 'assets' }
        try {
          rows.push({ ...row, ...(await this.action(id, remove)) })
        } catch (error) {
          rows.push({ ...row, bytes: null, failed: [problem(error)] })
        }
      }
      return rows
    })
  }
}
