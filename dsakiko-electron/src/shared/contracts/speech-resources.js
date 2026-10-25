/**
 * @typedef {Record<string, import('./common.js').AssetRef>} SpeechResourceFiles 文件相对于基础资源根目录的路径到引用的映射。
 * @typedef {{shared: SpeechResourceFiles, versions: Record<string, SpeechResourceFiles>}} SpeechResourceManifest 已配置版本和共享文件，切换架构不会移除其他版本。
 */

/** 只允许基础模型和 G2PW 文件，禁止路径穿越或把整个 models 根目录交给清理器。 */
export function validSpeechResourcePath(value) {
  return (
    typeof value === 'string' &&
    value.length <= 1000 &&
    !/[\\:\0]/u.test(value) &&
    value.split('/').every((part) => part && part !== '.' && part !== '..') &&
    ['GPT_SoVITS/pretrained_models/', 'GPT_SoVITS/text/G2PWModel/'].some((prefix) =>
      value.startsWith(prefix)
    )
  )
}

/** 校验逐文件清单；不读取文件系统，也不隐式修正或丢弃配置。 */
export function validSpeechResources(value) {
  function object(item) {
    return item && typeof item === 'object' && !Array.isArray(item)
  }
  function files(items) {
    return (
      object(items) &&
      Object.entries(items).every(
        ([name, ref]) =>
          validSpeechResourcePath(name) &&
          object(ref) &&
          Object.keys(ref).length === 1 &&
          typeof ref.id === 'string' &&
          /^[\p{L}\p{N}_-]{1,100}$/u.test(ref.id)
      )
    )
  }
  if (
    !object(value) ||
    Object.keys(value).length !== 2 ||
    !files(value.shared) ||
    !object(value.versions)
  )
    return false
  return Object.entries(value.versions).every(
    ([version, items]) =>
      /^[a-zA-Z0-9_-]{1,100}$/u.test(version) &&
      !['__proto__', 'constructor', 'prototype'].includes(version) &&
      files(items) &&
      Object.entries(items).every(
        ([name, ref]) => !value.shared[name] || value.shared[name].id === ref.id
      )
  )
}
