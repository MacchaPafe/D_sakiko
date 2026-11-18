/**
 * 将当前形态及其当时的默认服装固定到对话设置中。
 * @param {import('../../shared/contracts/characters.js').CharacterDefinition} role 角色配置。
 * @param {Partial<import('../../shared/contracts/settings.js').CharacterSelection>} selection 已有选择。
 * @returns {import('../../shared/contracts/settings.js').CharacterSelection} 可持久保存的选择。
 */
export function pinOutfit(role, selection = {}) {
  const formId = selection.formId ?? role.defaultFormId
  const form = role.forms.find((item) => item.id === formId)
  const modelOverrides = { ...selection.modelOverrides }
  if (form && modelOverrides[formId] == null) modelOverrides[formId] = form.defaultModelId
  return { formId, modelOverrides }
}
