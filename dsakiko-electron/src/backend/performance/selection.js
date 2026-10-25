// 与 Python expression_policy 的语义候选顺序保持一致；这里只用于自动回退。
const expressions = {
  idle: ['idle', 'exp_idle01', 'exp_idle02', 'exp_idle03'],
  serious: ['serious', 'exp_serious01', 'exp_serious02', 'exp_idle01'],
  happiness: ['exp_smile02', 'exp_smile01', 'exp_bsmile01', 'exp_kime01', 'exp_idle01'],
  like: ['exp_smile02', 'exp_shy01', 'exp_bsmile01', 'exp_smile01', 'exp_kime01', 'exp_idle01'],
  sadness: [
    'exp_sad01',
    'exp_sad02',
    'exp_cry01',
    'exp_cry02',
    'exp_pale01',
    'exp_serious01',
    'exp_idle01'
  ],
  anger: [
    'exp_angry01',
    'exp_angry02',
    'exp_hatred01',
    'exp_upset01',
    'exp_serious02',
    'exp_serious01',
    'exp_idle01'
  ],
  disgust: [
    'exp_upset01',
    'exp_sneer01',
    'exp_smirk01',
    'exp_serious02',
    'exp_angry01',
    'exp_sad01',
    'exp_idle01'
  ],
  surprise: [
    'exp_surprised01',
    'exp_surprised02',
    'exp_amazed01',
    'exp_pale01',
    'exp_upset01',
    'exp_idle01'
  ],
  fear: [
    'exp_pale01',
    'exp_dispair01',
    'exp_surprised01',
    'exp_cry01',
    'exp_sad01',
    'exp_serious01',
    'exp_idle01'
  ],
  text_generating: ['exp_thinking01', 'exp_thinking02', 'exp_serious01', 'exp_idle01']
}
const legacyEmotions = ['happiness', 'sadness', 'anger', 'disgust', 'like', 'surprise', 'fear']
const activityGroups = {
  idle: 'idle_motion',
  thinking: 'text_generating',
  'voice-input': 'talking_motion'
}

/**
 * 按实际目标模型解析演出，两个通道独立回退，不覆盖历史选择。
 * @param {import('../characters/model-catalog.js').ModelCatalog | object} catalog 实际目标模型目录。
 * @param {import('../../shared/contracts/presentation.js').PerformanceSelection | null} selection 原始选择。
 * @param {string | null} emotion 台词情绪。
 * @param {{direction?: 'C' | 'L' | 'R', activity?: 'idle' | 'thinking' | 'voice-input' | null}} options 宿主方向和运行状态。
 * @returns {import('../../shared/contracts/presentation.js').ResolvedPerformance} 可执行的包内资源与回退说明。
 */
export function resolvePerformance(
  catalog,
  selection,
  emotion,
  { direction = 'C', activity = null } = {}
) {
  const normalized = String(emotion ?? '')
    .trim()
    .toLowerCase()
  const emotionGroup =
    legacyEmotions[Number(normalized.match(/^label_([0-6])$/u)?.[1])] ?? normalized
  const group =
    activityGroups[activity] ?? (legacyEmotions.includes(emotionGroup) ? emotionGroup : 'happiness')
  const result = {
    version: catalog.version ?? null,
    group,
    motion: null,
    expression: null,
    fallbackReasons: []
  }
  function variant(id) {
    const variants = catalog.motions?.[id]?.variants
    return variants?.[direction] ?? variants?.[''] ?? variants?.C
  }
  // v2 动作自带面部曲线，普通台词忽略历史 v3 的两项显式选择。
  const explicit = catalog.version === 'v3' && !activity
  let motion = explicit && variant(selection?.motion) ? selection.motion : null
  if (!motion) {
    if (explicit && selection?.motion && selection.motion !== 'auto')
      result.fallbackReasons.push('motion_unavailable')
    const stateMotions = new Set(
      Object.values(catalog.states ?? {}).flatMap((state) => [state.on?.motion, state.off?.motion])
    )
    const pools = [
      catalog.groups?.[group + '_' + direction],
      catalog.groups?.[group],
      catalog.groups?.idle_motion,
      catalog.groups?.IDLE,
      Object.keys(catalog.motions ?? {})
    ]
    for (const pool of pools) {
      motion = pool?.find((id) => !stateMotions.has(id) && variant(id))
      if (motion) break
    }
  }
  if (motion) result.motion = { id: motion, ...variant(motion) }
  if (catalog.version === 'v3') {
    let expression =
      explicit && catalog.expressions?.[selection?.expression] ? selection.expression : null
    if (!expression) {
      if (explicit && selection?.expression && selection.expression !== 'auto')
        result.fallbackReasons.push('expression_unavailable')
      expression = [...(expressions[group] ?? []), ...expressions.idle].find(
        (id) => catalog.expressions?.[id]
      )
    }
    if (expression)
      result.expression = { id: expression, member: catalog.expressions[expression].member }
  }
  return result
}
