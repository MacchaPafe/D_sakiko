const measuredContentVersion = '5e7d644f-af0b-406a-9e45-191fa3b630c5'
// 与发布归档绑定。重新打包后版本会变，新的模型可按正常路径重新测量。
const measures = {
  sakiko: {
    white: { left: 1875, top: 1312.5, width: 2250, height: 7312.5 },
    black: { left: 1558.59375, top: 1312.5, width: 2882.8125, height: 7464.84375 }
  },
  anon: { default: { left: 2097.65625, top: 1183.59375, width: 2039.0625, height: 7675.78125 } },
  soyo: { default: { left: 1898.4375, top: 1160.15625, width: 2214.84375, height: 7652.34375 } }
}

/**
 * @param {string | undefined} contentVersion 归档的内容版本。
 * @param {string} characterId 内置角色。
 * @param {string} formId 角色形态。
 * @returns {import('../../shared/contracts/presentation.js').ModelBounds | undefined} 已测量的可见范围。
 */
export function builtinModelBounds(contentVersion, characterId, formId) {
  if (contentVersion === measuredContentVersion && Object.hasOwn(measures, characterId)) {
    const forms = measures[characterId]
    if (Object.hasOwn(forms, formId)) return forms[formId]
  }
}
