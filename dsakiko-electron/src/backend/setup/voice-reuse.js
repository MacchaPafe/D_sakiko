import { createHash } from 'node:crypto'
import { extractAssetReferences } from '../resources/references.js'
import { fingerprint, requireValue } from '../core/support.js'
import { sha256, stamp } from './files.js'
import { resourceFetch } from './download.js'

/** 以实际内容复用导入材料。硬链接外部文件发生变化时，原缓存不再适用。 */
export class VoiceReuse {
  constructor(assets, transport) {
    this.assets = assets
    this.transport = transport
    this.hashes = new Map()
    this.definitions = new Map()
  }

  async matchingFiles(component, voices, previous = {}) {
    const candidates = new Map(
      extractAssetReferences([voices, previous]).map((ref) => [ref.id, ref])
    )
    const files = component.files.filter((file) => !file.path.endsWith('/voice.json'))
    const refs = {}
    for (const ref of candidates.values()) {
      try {
        const local = await this.assets.localPath(ref)
        const before = await stamp(local)
        const eligible = files.filter((file) => file.sizeBytes === before.size)
        if (!eligible.length) continue
        const identity = fingerprint(before)
        let digest = this.hashes.get(identity)
        if (!digest) {
          digest = await sha256(local)
          if (fingerprint(await stamp(local)) !== identity) continue
          this.hashes.set(identity, digest)
        }
        for (const file of eligible) {
          if (file.sha256 === digest) refs[file.path] ??= ref
        }
      } catch {
        // 资源离线或失效时由安装补齐。
      }
    }
    return refs
  }

  async definition(component) {
    const metadata = component.files.find(
      (file) => file.path === component.characterId + '/voice.json'
    )
    if (!metadata || metadata.sizeBytes > 65536) return null
    if (this.definitions.has(metadata.sha256)) return this.definitions.get(metadata.sha256)
    const response = await resourceFetch(metadata.source, metadata.path, {
      fetch: this.transport,
      signal: AbortSignal.timeout(10000)
    })
    requireValue(response.ok, 'voice_manifest_invalid', '无法读取角色语音版本资料。')
    const chunks = []
    let length = 0
    for await (const chunk of response.body) {
      length += chunk.length
      requireValue(length <= metadata.sizeBytes, 'voice_manifest_invalid')
      chunks.push(Buffer.from(chunk))
    }
    const bytes = Buffer.concat(chunks)
    requireValue(
      length === metadata.sizeBytes &&
        createHash('sha256').update(bytes).digest('hex') === metadata.sha256,
      'voice_manifest_invalid'
    )
    const definition = JSON.parse(bytes.toString('utf8'))
    this.definitions.set(metadata.sha256, definition)
    return definition
  }
}

/** 转为稳定的业务配置。下载和本地识别都根据同一个发布定义解析。 */
export function voiceDefinition(component, refs, voice) {
  requireValue(
    voice?.schemaVersion === 1 &&
      voice.characterId === component.characterId &&
      ['v4', 'v2ProPlus'].includes(voice.architecture) &&
      voice.forms &&
      voice.weights,
    'voice_manifest_invalid'
  )
  const prefix = component.characterId + '/'
  function asset(name) {
    return typeof name === 'string' && Object.hasOwn(refs, prefix + name)
      ? refs[prefix + name]
      : null
  }
  function reference(value) {
    requireValue(
      typeof value?.text === 'string' && typeof value.language === 'string',
      'voice_manifest_invalid'
    )
    return { audio: asset(value.audio), text: value.text, language: value.language }
  }
  const forms = {}
  for (const [id, definition] of Object.entries(voice.forms)) {
    const entry = {
      gptWeights: asset(voice.weights.gpt),
      sovitsWeights: asset(voice.weights.sovits),
      defaultReference: reference(definition.defaultReference),
      emotions: Object.fromEntries(
        Object.entries(definition.emotions ?? {}).map(([name, value]) => [name, reference(value)])
      )
    }
    if (
      entry.gptWeights &&
      entry.sovitsWeights &&
      entry.defaultReference.audio &&
      Object.values(entry.emotions).every((reference) => reference.audio)
    )
      forms[id] = entry
  }
  return { architecture: voice.architecture, forms }
}
