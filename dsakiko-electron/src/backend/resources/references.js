/** @typedef {import('../../shared/contracts/common.js').AssetRef} AssetRef */

/**
 * 扫描任意数据对象，提取其中包含的所有媒体资源引用（AssetRef）。
 * @param {unknown} value 待扫描的数据对象。
 * @returns {AssetRef[]} 去重后的资源引用列表。
 */
export function extractAssetReferences(value) {
  const found = new Map()
  const visited = new WeakSet()
  function visit(item) {
    if (!item || typeof item !== 'object' || visited.has(item)) return
    visited.add(item)
    // 为什么要求对象仅含唯一键 id 才算 AssetRef：
    // 对话中的 Message、Turn 等结构也都带有 id 字段，限制仅含单个 id 属性的对象才能精确识别出 AssetRef，避免把普通业务对象的 ID 误当成文件资源。
    if (!Array.isArray(item) && Object.keys(item).length === 1 && typeof item.id === 'string')
      found.set(item.id, { id: item.id })
    else Object.values(item).forEach(visit)
  }
  visit(value)
  return [...found.values()]
}
