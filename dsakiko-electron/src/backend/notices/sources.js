/**
 * 将业务持久结果转换为通知来源，避免前端承担事件恢复或保存业务文案。
 * @param {{onboarding: import('../onboarding/interface.js').Onboarding, setup: import('../setup/interface.js').ResourceSetup, settings: import('../settings/interface.js').Settings, characters: import('../characters/interface.js').CharacterCatalog}} modules 只读取所需业务事实。
 * @returns {Promise<import('./interface.js').NoticeSource[]>} 当前仍有效的全部来源；查询失败整体拒绝，不能误作来源已消失。
 */
export async function readNoticeSources({ onboarding, setup, settings, characters }) {
  const sources = []
  const state = await onboarding.getState()
  if (
    state.initialized &&
    (state.connectionState === 'missing' ||
      (state.path === 'quick' && state.connectionState === 'builtin'))
  ) {
    sources.push({
      key: 'onboarding:connection',
      kind: 'connection',
      createdAt: state.completedAt ?? 0,
      data: { connectionState: state.connectionState }
    })
  }
  const milestones = await setup.listVoiceNoticeMilestones()
  if (!milestones.length) return sources
  const features = await setup.getFeatureState()
  const speechEnabled = await settings.get('speech.enabled')
  for (const milestone of milestones) {
    const role = await characters.get(milestone.characterId)
    if (!role) continue
    const ready = features.characters.filter(
      (form) =>
        form.characterId === role.id && milestone.formIds.includes(form.formId) && form.ready
    )
    if (!ready.length) continue
    sources.push({
      key: 'setup:' + milestone.jobId + ':voice:' + role.id,
      kind: 'voice-ready',
      createdAt: milestone.createdAt,
      data: {
        jobId: milestone.jobId,
        characterId: role.id,
        characterName: role.name,
        readyFormIds: ready.map((form) => form.formId),
        enabled: speechEnabled || ready.some((form) => form.autoEnabled)
      }
    })
  }
  return sources
}
