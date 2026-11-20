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

// 原始包可能只有 Default 组；沿用 Python 标准情绪组的主要动作词。
const theatreMotionNames = {
  happiness: ['smile', 'kime', 'wink'],
  like: ['smile', 'kime', 'wink'],
  anger: ['angry'],
  sadness: ['sad', 'cry'],
  disgust: ['denial', 'confrict'],
  surprise: ['surprised', 'question'],
  fear: ['nervous'],
  text_generating: ['thinking', 'check']
}

function variant(catalog, id, direction) {
  const variants = catalog.motions?.[id]?.variants
  return variants?.[direction] ?? variants?.[''] ?? variants?.C
}

/** @param {import('../characters/model-catalog.js').ModelCatalog} catalog 模型目录。 @param {import('../../shared/contracts/presentation.js').ResolvedPerformance} plan 首个动作的解析结果。 @param {string} direction 当前方向。 @returns {import('../../shared/contracts/presentation.js').ResolvedMotion|null} 可安全重复的动作。 */
export function resolveFollowUpMotion(catalog, plan, direction) {
  const primary = plan.motion
  const stateMotions = new Set(
    Object.values(catalog.states ?? {}).flatMap((state) => [state.on?.motion, state.off?.motion])
  )
  // 状态动作只走自己的计划与结算；补播动作不应再次触发面具等状态变更。
  const canRepeat = (id) => !stateMotions.has(id) && !catalog.stateEffects?.motions?.[id]?.length
  if (plan.version === 'v2') {
    const ids = [
      ...(catalog.groups?.[plan.group + '_' + direction] ?? []),
      ...(catalog.groups?.[plan.group] ?? [])
    ]
    for (const id of ids) {
      if (!canRepeat(id)) continue
      const candidate = variant(catalog, id, direction)
      if (candidate?.durationMs && candidate.member !== primary?.member) return { id, ...candidate }
    }
  }
  return primary && canRepeat(primary.id) ? primary : null
}

/**
 * 按实际目标模型解析演出，两个通道独立回退，不覆盖历史选择。
 * @param {import('../characters/model-catalog.js').ModelCatalog | object} catalog 实际目标模型目录。
 * @param {import('../../shared/contracts/presentation.js').PerformanceSelection | null} selection 原始选择。
 * @param {string | null} emotion 台词情绪。
 * @param {{direction?: 'C' | 'L' | 'R', theatre?: boolean, activity?: 'idle' | 'thinking' | 'voice-input' | null}} options 宿主方向和运行状态。
 * @returns {import('../../shared/contracts/presentation.js').ResolvedPerformance} 可执行的包内资源与回退说明。
 */
export function resolvePerformance(
  catalog,
  selection,
  emotion,
  { direction = 'C', activity = null, theatre = false } = {}
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
  // v2 动作自带面部曲线，普通台词忽略历史 v3 的两项显式选择。
  const explicit = catalog.version === 'v3' && !activity
  const inward = theatre && direction !== 'C'
  function playable(id) {
    if (inward) return catalog.motions?.[id]?.variants?.[direction]
    return variant(catalog, id, direction)
  }
  let motion = explicit && playable(selection?.motion) ? selection.motion : null
  if (!motion) {
    if (explicit && selection?.motion && selection.motion !== 'auto')
      result.fallbackReasons.push('motion_unavailable')
    const stateMotions = new Set(
      Object.values(catalog.states ?? {}).flatMap((state) => [state.on?.motion, state.off?.motion])
    )
    const ids = Object.keys(catalog.motions ?? {})
    const pools = [
      catalog.groups?.[group + '_' + direction],
      catalog.groups?.[group],
      theatre &&
        catalog.groups?.Default &&
        ids.filter((id) => {
          const words = id.toLowerCase().split(/[^a-z]+/u)
          return theatreMotionNames[group]?.some((word) => words.includes(word))
        }),
      catalog.groups?.['idle_motion_' + direction],
      catalog.groups?.idle_motion,
      catalog.groups?.IDLE
    ]
    // 原始 V3 包常把所有文件放在 Default；用逻辑名识别基础待机。
    if (theatre) pools.push(ids.filter((id) => /(?:^|_)idle[0-9]*(?:_|$)/iu.test(id)))
    if (!theatre || (catalog.version === 'v2' && activity !== 'idle')) pools.push(ids)
    for (const pool of pools) {
      if (!pool) continue
      motion = pool.find((id) => !stateMotions.has(id) && playable(id))
      if (motion) break
    }
  }
  if (motion) result.motion = { id: motion, ...playable(motion) }
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
