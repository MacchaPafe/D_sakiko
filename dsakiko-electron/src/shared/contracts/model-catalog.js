/**
 * 将两代模型文件中的动作与表情投影为共同目录。
 * @param {object} model 模型 JSON。
 * @returns {{motions: object[], expressions: object[]}} 含逻辑编号和底层位置的目录。
 */
export function readModelCatalog(model) {
  const groups = model.FileReferences?.Motions || model.motions || {}
  const expressions = model.FileReferences?.Expressions || model.expressions || []
  const motions = []
  for (const [group, items] of Object.entries(groups)) {
    if (!Array.isArray(items)) continue
    items.forEach((item, index) => {
      motions.push({
        id: item.LogicalId || `${group}:${index}`,
        description: item.Description || group,
        group,
        index
      })
    })
  }
  return {
    motions,
    expressions: expressions.map((entry, index) => ({
      id: entry.Name || entry.name || String(index),
      description: entry.Description || entry.Name || entry.name || '表情',
      index
    }))
  }
}
