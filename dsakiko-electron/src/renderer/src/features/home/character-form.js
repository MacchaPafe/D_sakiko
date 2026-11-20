/** @param {object} definition 角色定义。 @param {object} item 当前形态。 @returns {string} 可区分同名形态的界面名称。 */
export function characterFormName(definition, item) {
  if (definition.bangDreamId === 'sakiko') {
    if (item.id === 'black') return '黑祥'
    if (item.id === 'white') return '白祥'
  }
  const duplicate = definition.forms.some(
    (candidate) => candidate.id !== item.id && candidate.displayName === item.displayName
  )
  return duplicate ? item.displayName + ' · ' + item.id : item.displayName
}
