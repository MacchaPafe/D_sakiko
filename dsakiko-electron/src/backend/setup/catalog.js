import path from 'node:path'
import { readJson, requireValue, copy } from '../core/support.js'
import { relativeFile } from './files.js'
import { resourceFetch } from './download.js'

export const setupGroups = [
  {
    id: 'asr',
    name: '语音识别模型',
    description: '用于语音输入',
    options: [{ id: 'asr', name: 'Whisper Small' }]
  },
  {
    id: 'speech',
    name: '语音合成模型',
    description: '让角色开口说话',
    options: [{ id: 'gsv', name: 'GPT-SoVITS' }]
  },
  {
    id: 'voices',
    name: '角色语音',
    options: [
      { id: 'voice.anon', name: '爱音' },
      { id: 'voice.soyo', name: '素世' },
      { id: 'voice.sakiko', name: '祥子' }
    ]
  }
]

/** 对发布清单作结构与路径验证，文件哈希只承担内容完整性校验。 */
export function validateRelease(release) {
  requireValue(
    !release?.setupApiVersion || release.setupApiVersion === 1,
    'resource_app_upgrade_required',
    '资源需要较新的主程序，请先升级。'
  )
  requireValue(
    release?.schemaVersion === 1 &&
      typeof release.version === 'string' &&
      Array.isArray(release.files) &&
      release.files.length <= 10000,
    'resource_manifest_invalid'
  )
  const paths = new Set()
  for (const file of release.files) {
    const name = relativeFile(file.path)
    requireValue(
      !paths.has(name.toLowerCase()) &&
        Number.isSafeInteger(file.sizeBytes) &&
        file.sizeBytes >= 0 &&
        /^[a-f0-9]{64}$/.test(file.sha256),
      'resource_manifest_invalid'
    )
    paths.add(name.toLowerCase())
  }
  return release
}

/** 环境声明仅能选择固定安装器和目标平台，不能携带可执行命令。 */
export function runtimePackages(manifest) {
  requireValue(
    manifest?.schemaVersion === 1 && Array.isArray(manifest.packages),
    'resource_manifest_invalid'
  )
  const result = {}
  for (const item of manifest.packages) {
    requireValue(
      ['asr', 'gsv'].includes(item.kind) && typeof item.target === 'string',
      'resource_manifest_invalid'
    )
    const targets =
      item.kind === 'asr'
        ? ['win32-x64', 'darwin-arm64']
        : ['win32-x64-cpu', 'win32-x64-cuda', 'darwin-arm64-cpu']
    requireValue(
      targets.includes(item.target) &&
        Number.isSafeInteger(item.installBytes) &&
        item.installBytes >= 0,
      'resource_manifest_invalid'
    )
    validateRelease({ ...item, schemaVersion: 1 })
    requireValue(
      item.files.length > 0 &&
        item.files.every(
          (file) =>
            file.archive === 'zip' &&
            Number.isSafeInteger(file.expandedBytes) &&
            file.expandedBytes > 0
        ),
      'resource_manifest_invalid'
    )
    requireValue(
      item.setupApiVersion === 1,
      'resource_app_upgrade_required',
      '运行资源需要较新的主程序，请先升级。'
    )
    const key = item.kind + '.' + item.target
    requireValue(!result[key], 'resource_manifest_invalid')
    result[key] = {
      version: item.version,
      available: true,
      files: item.files.map((file) => ({
        path: file.path,
        sizeBytes: file.sizeBytes,
        sha256: file.sha256,
        archive: 'zip',
        expandedBytes: file.expandedBytes,
        source: 'runtimes'
      })),
      installBytes: item.installBytes
    }
  }
  return result
}

/** 将发布组件封装在三个产品分组之后；运行包必须由发布端明确提供。 */
export class SetupCatalog {
  constructor({
    directory,
    fetch: transport = fetch,
    platform = process.platform,
    arch = process.arch,
    runtimes = {}
  }) {
    Object.assign(this, { directory, transport, platform, arch, runtimes })
    this.releases = null
  }

  async components({ refresh = false, accelerator = 'cpu' } = {}) {
    requireValue(['cpu', 'cuda'].includes(accelerator), 'unsupported_accelerator')
    const target = `${this.platform}-${this.arch}`
    requireValue(
      ['win32-x64', 'darwin-arm64'].includes(target) &&
        (accelerator === 'cpu' || target === 'win32-x64'),
      'unsupported_platform',
      '当前仅支持 Windows x64 和 macOS arm64。'
    )
    const releases = copy(
      this.releases ?? (await readJson(path.join(this.directory, 'published-packages.json')))
    )
    requireValue(releases, 'resource_catalog_missing')
    if (refresh) {
      for (const source of ['voices', 'asr']) {
        const response = await resourceFetch(source, 'release.json', {
          fetch: this.transport,
          signal: AbortSignal.timeout(30000)
        })
        requireValue(response.ok, 'resource_catalog_unavailable')
        const text = await response.text()
        requireValue(text.length <= 2 * 1024 * 1024, 'resource_manifest_invalid')
        const next = validateRelease(JSON.parse(text))
        requireValue(next.packageId === releases[source].packageId, 'resource_manifest_invalid')
        releases[source] = next
      }
      const response = await resourceFetch('runtimes', 'release.json', {
        fetch: this.transport,
        signal: AbortSignal.timeout(30000)
      })
      if (response.status !== 404) {
        requireValue(response.ok, 'resource_catalog_unavailable')
        this.runtimes = runtimePackages(await response.json())
      } else await response.body?.cancel()
      this.releases = releases
    }
    for (const release of Object.values(releases)) validateRelease(release)
    const runtime = (kind, variant) => ({
      id: `${kind}.runtime`,
      kind: 'runtime',
      requires: [],
      files: [],
      available: false,
      unavailableReason: '该平台运行环境尚未发布',
      ...copy(this.runtimes[`${kind}.${variant}`] ?? {}),
      target: variant
    })
    const result = [
      runtime('asr', target),
      runtime('gsv', `${target}-${accelerator}`),
      {
        id: 'asr.models',
        kind: 'asr',
        version: releases.asr.version,
        available: true,
        requires: [],
        files: releases.asr.files.map((file) => ({ ...file, source: 'asr' }))
      },
      {
        id: 'gsv.models',
        kind: 'gsv',
        version: '2026-11-01',
        available: true,
        requires: [],
        files: await readJson(path.join(this.directory, 'gsv-files.json'))
      }
    ]
    for (const id of ['anon', 'soyo', 'sakiko'])
      result.push({
        id: 'voice.' + id,
        kind: 'voice',
        characterId: id,
        version: releases.voices.version,
        available: true,
        requires: ['gsv.models', 'gsv.runtime'],
        files: releases.voices.files
          .filter((file) => file.path.startsWith(id + '/'))
          .map((file) => ({ ...file, source: 'voices' }))
      })
    return result
  }

  async select(selection, options = {}) {
    requireValue(
      Array.isArray(selection) &&
        selection.every((id) =>
          ['asr', 'gsv', 'voice.anon', 'voice.soyo', 'voice.sakiko'].includes(id)
        ),
      'invalid_resource_selection'
    )
    const components = await this.components(options)
    const ids = new Set()
    function add(id) {
      if (ids.has(id)) return
      const item = components.find((value) => value.id === id)
      requireValue(item, 'resource_component_missing')
      for (const dependency of item.requires) add(dependency)
      ids.add(id)
    }
    for (const id of selection) {
      if (id === 'asr' || id === 'gsv') {
        add(id + '.runtime')
        add(id + '.models')
      } else add(id)
    }
    return [...ids].map((id) => components.find((item) => item.id === id))
  }
}
