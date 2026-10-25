/**
 * @typedef {import('./common.js').AssetRef} GptSovitsFile
 * @typedef {{config?: GptSovitsFile, weights?: GptSovitsFile, tokenizer?: GptSovitsFile}} BertFiles
 * @typedef {{config?: GptSovitsFile, weights?: GptSovitsFile, featureExtractor?: GptSovitsFile}} HubertFiles
 * @typedef {{model?: GptSovitsFile, config?: GptSovitsFile, polyphonicCharacters?: GptSovitsFile, monophonicCharacters?: GptSovitsFile, bopomofoToPinyin?: GptSovitsFile, characterBopomofo?: GptSovitsFile}} G2pwFiles
 * @typedef {{baseSovits?: GptSovitsFile, vocoder?: GptSovitsFile, speakerEncoder?: GptSovitsFile}} GptSovitsVersionFiles
 * @typedef {{shared: {bert?: BertFiles, hubert?: HubertFiles, g2pw?: G2pwFiles, languageDetector?: {model?: GptSovitsFile}}, versions: Record<string, GptSovitsVersionFiles>}} GptSovitsResourceManifest 用途到资源引用的映射，不约束物理文件名或来源。
 */

export const gptSovitsArchitectures = ['v1', 'v2', 'v4', 'v2Pro', 'v2ProPlus']
export const gptSovitsSharedFiles = {
  bert: ['config', 'weights', 'tokenizer'],
  hubert: ['config', 'weights', 'featureExtractor'],
  g2pw: [
    'model',
    'config',
    'polyphonicCharacters',
    'monophonicCharacters',
    'bopomofoToPinyin',
    'characterBopomofo'
  ],
  languageDetector: ['model']
}
const versionFiles = ['baseSovits', 'vocoder', 'speakerEncoder']

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function files(value, names) {
  return (
    object(value) &&
    Object.entries(value).every(
      ([name, ref]) =>
        names.includes(name) &&
        object(ref) &&
        Object.keys(ref).length === 1 &&
        typeof ref.id === 'string' &&
        /^[\p{L}\p{N}_-]{1,100}$/u.test(ref.id)
    )
  )
}

/**
 * 保存时允许逐步配置，但拒绝未知用途和旧路径清单。
 * @param {unknown} value 待保存的 GPT-SoVITS 资源清单。
 * @returns {boolean} 是否符合逐用途引用契约。
 */
export function validGptSovitsResources(value) {
  return (
    object(value) &&
    Object.keys(value).length === 2 &&
    object(value.shared) &&
    object(value.versions) &&
    Object.entries(value.shared).every(
      ([group, entries]) =>
        Object.hasOwn(gptSovitsSharedFiles, group) && files(entries, gptSovitsSharedFiles[group])
    ) &&
    Object.entries(value.versions).every(
      ([version, entries]) =>
        gptSovitsArchitectures.includes(version) && files(entries, versionFiles)
    )
  )
}

/**
 * 启动时固定当前架构的文件集合，完整性要求不妨碍保存未完成的配置。
 * @param {GptSovitsResourceManifest} manifest 已通过设置校验的清单。
 * @param {string} architecture 当前架构。
 * @returns {{files: object, missing: string[]}} 当前进程所需引用与缺失用途。
 */
export function selectGptSovitsFiles(manifest, architecture) {
  const missing = []
  for (const [group, names] of Object.entries(gptSovitsSharedFiles)) {
    for (const name of names) {
      if (!manifest.shared[group]?.[name]) missing.push(`${group}.${name}`)
    }
  }
  const version = manifest.versions[architecture] ?? {}
  if (architecture === 'v4' && !version.vocoder) missing.push('vocoder')
  if (['v2Pro', 'v2ProPlus'].includes(architecture) && !version.speakerEncoder)
    missing.push('speakerEncoder')
  // baseSovits 仅在角色使用 LoRA 时必需，交给读取角色权重的加载器验证。
  return { files: { ...manifest.shared, ...version }, missing }
}
