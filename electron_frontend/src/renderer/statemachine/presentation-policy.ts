/**
 * Electron-local Live2D presentation selection.
 *
 * The backend supplies business facts (character and Sakiko state), while this
 * policy inspects the loaded model manifest and makes the runtime choice.  It
 * deliberately has no dependency on the Python control plane or renderer facts.
 */
export type SakikoPresentationState = 'black' | 'white' | undefined
/** Backend-provided base is a fallback fact, not a presentation controller. */
export type BasePresentation = 'idle' | 'serious'
/** Cubism runtime family determined from the manifest layout, not its version. */
export type Live2DRuntimeKind = 'v2' | 'v3'

export interface Live2DPresentationMetadata {
  /** `FileReferences` is the Cubism 3/4 layout; root `version` is unreliable. */
  runtimeKind: Live2DRuntimeKind
  /** The actual manifest group name is the key; each array keeps its file index. */
  motionFilesByGroup: Record<string, Array<string | null>>
  /** Expression identifiers accepted by `model.expression()`. */
  expressionIds: string[]
}

export interface ExpressionSelection {
  expression: string | null
  source: 'motion' | 'semantic' | 'base' | 'idle' | 'none'
}

export interface SakikoMaskTransition {
  requestedGroup: 'change_character_maskoff' | 'maskon'
  maskOn: boolean
}

export interface SakikoBlackEntryTransition {
  requestedGroup: 'change_character' | 'change_character_maskoff'
  maskOn: boolean
}

type JsonRecord = Record<string, unknown>

const MOTION_TOKEN_EXPRESSION_RULES: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['smile', ['exp_smile02', 'exp_smile01', 'exp_bsmile01']],
  ['kime', ['exp_kime01', 'exp_smile01']],
  ['wink', ['exp_smile01', 'exp_shy01']],
  ['cry', ['exp_cry01', 'exp_sad01']],
  ['sad', ['exp_sad01']],
  ['angry', ['exp_angry01']],
  ['denial', ['exp_upset01', 'exp_sneer01', 'exp_serious02', 'exp_angry01']],
  ['question', ['exp_surprised01']],
  ['surprised', ['exp_surprised01']],
  ['nervous', ['exp_pale01', 'exp_dispair01']],
  ['thinking', ['exp_thinking01', 'exp_serious01']],
  ['check', ['exp_thinking01', 'exp_serious01']],
  ['serious', ['exp_serious01', 'exp_serious02']],
  ['look', ['exp_idle01']],
  ['idle', ['exp_idle01']],
  ['bye', ['exp_smile01', 'exp_bsmile01', 'exp_idle01']],
]

export const SEMANTIC_EXPRESSION_CANDIDATES: Readonly<Record<string, readonly string[]>> = {
  idle: ['idle', 'exp_idle01', 'exp_idle02', 'exp_idle03'],
  serious: ['serious', 'exp_serious01', 'exp_serious02', 'exp_idle01'],
  happiness: ['exp_smile02', 'exp_smile01', 'exp_bsmile01', 'exp_kime01', 'exp_idle01'],
  like: ['exp_smile02', 'exp_shy01', 'exp_bsmile01', 'exp_smile01', 'exp_kime01', 'exp_idle01'],
  sadness: ['exp_sad01', 'exp_sad02', 'exp_cry01', 'exp_cry02', 'exp_pale01', 'exp_serious01', 'exp_idle01'],
  anger: ['exp_angry01', 'exp_angry02', 'exp_hatred01', 'exp_upset01', 'exp_serious02', 'exp_serious01', 'exp_idle01'],
  disgust: ['exp_upset01', 'exp_sneer01', 'exp_smirk01', 'exp_serious02', 'exp_angry01', 'exp_sad01', 'exp_idle01'],
  surprise: ['exp_surprised01', 'exp_surprised02', 'exp_amazed01', 'exp_pale01', 'exp_upset01', 'exp_idle01'],
  fear: ['exp_pale01', 'exp_dispair01', 'exp_surprised01', 'exp_cry01', 'exp_sad01', 'exp_serious01', 'exp_idle01'],
  text_generating: ['exp_thinking01', 'exp_thinking02', 'exp_serious01', 'exp_idle01'],
}

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null
}

function stringField(value: unknown, keys: string[]): string | null {
  const record = asRecord(value)
  if (!record) return typeof value === 'string' && value ? value : null
  for (const key of keys) {
    const candidate = record[key]
    if (typeof candidate === 'string' && candidate) return candidate
  }
  return null
}

function expressionIdFromPath(value: string): string {
  const filename = value.replace(/\\/g, '/').split('/').pop() || value
  return filename.replace(/\.exp3?\.json$/i, '')
}

/** Parse both Cubism 2 and Cubism 3/4 model settings without normalizing IDs. */
export function parseLive2DPresentationMetadata(definition: unknown): Live2DPresentationMetadata {
  const root = asRecord(definition)
  if (!root) return { runtimeKind: 'v2', motionFilesByGroup: {}, expressionIds: [] }
  // Some real Cubism 2 manifests report a root `version` such as 3.1.  The
  // layout is the reliable signal: only Cubism 3/4 has `FileReferences`.
  const runtimeKind: Live2DRuntimeKind = Object.prototype.hasOwnProperty.call(root, 'FileReferences')
    ? 'v3'
    : 'v2'
  const references = asRecord(root.FileReferences)
  const motionDefinitions = runtimeKind === 'v3'
    ? asRecord(references?.Motions) ?? {}
    : asRecord(root.motions) ?? {}
  const expressionDefinitions = runtimeKind === 'v3'
    ? Array.isArray(references?.Expressions) ? references.Expressions : []
    : Array.isArray(root.expressions) ? root.expressions : []

  const motionFilesByGroup: Record<string, Array<string | null>> = {}
  for (const [group, entries] of Object.entries(motionDefinitions)) {
    if (!Array.isArray(entries)) continue
    motionFilesByGroup[group] = entries.map(entry => stringField(entry, ['File', 'file']))
  }

  const expressionIds: string[] = []
  for (const expression of expressionDefinitions) {
    const namedId = typeof expression === 'string'
      ? null
      : stringField(expression, ['Name', 'name', 'Id', 'id'])
    const fileId = typeof expression === 'string'
      ? expressionIdFromPath(expression)
      : (() => {
        const file = stringField(expression, ['File', 'file'])
        return file ? expressionIdFromPath(file) : null
      })()
    const id = namedId || fileId
    if (id && !expressionIds.includes(id)) expressionIds.push(id)
  }
  return { runtimeKind, motionFilesByGroup, expressionIds }
}

export function baseSemanticForModel(
  modelKey: string,
  sakikoState: SakikoPresentationState,
  bridgeBase?: BasePresentation,
): BasePresentation {
  if (modelKey.trim().toLowerCase() === 'sakiko') {
    if (sakikoState === 'black') return 'serious'
    if (sakikoState === 'white') return 'idle'
  }
  return bridgeBase || 'idle'
}

/** Mirror the upstream black-Sakiko mask toggle without renderer callbacks. */
export function nextSakikoMaskTransition(maskOn: boolean): SakikoMaskTransition {
  return {
    requestedGroup: maskOn ? 'change_character_maskoff' : 'maskon',
    maskOn: !maskOn,
  }
}

/**
 * Match upstream's black-Sakiko conversion: choose a fresh local mask state
 * and use the matching entry animation.  A supplied business fact wins, but
 * in normal standalone operation the random choice stays Electron-local.
 */
export function selectBlackSakikoEntry(
  maskOn: boolean | undefined,
  random: () => number = Math.random,
): SakikoBlackEntryTransition {
  const selectedMaskOn = typeof maskOn === 'boolean' ? maskOn : random() < 0.5
  return {
    requestedGroup: selectedMaskOn ? 'change_character' : 'change_character_maskoff',
    maskOn: selectedMaskOn,
  }
}

function normalizedNameTokens(value: string): string[] {
  const fileName = value.split(/[\\/]/).pop() || value
  return fileName.toLowerCase().split(/[^0-9a-zA-Z]+/)
    .filter(Boolean)
    .map(token => token.replace(/\d+$/, ''))
    .filter(Boolean)
}

function expressionCandidatesForTokens(tokens: Iterable<string>): readonly string[] {
  const tokenSet = new Set(tokens)
  for (const [token, candidates] of MOTION_TOKEN_EXPRESSION_RULES) {
    if (tokenSet.has(token)) return candidates
  }
  return []
}

function supportedExpression(candidates: Iterable<string>, supported: ReadonlySet<string>): string | null {
  for (const candidate of candidates) {
    if (supported.has(candidate)) return candidate
  }
  return null
}

/**
 * Select a manifest-supported expression for one concrete motion.  The order
 * matches the upstream runtime policy: motion filename, semantic group, group
 * tokens, token semantics, then the idle candidates. The current character
 * base is not part of this per-motion selector; the caller owns explicit base
 * presentation changes. If no supported expression exists, upstream leaves
 * the current expression untouched.
 */
export function selectExpressionForMotion(
  metadata: Live2DPresentationMetadata,
  group: string,
  motionIndex: number,
  baseSemantic: 'idle' | 'serious',
): ExpressionSelection {
  // Cubism 2 manifests in the bundled application have a deliberately small
  // expression vocabulary (the real Sakiko costume has only serious/idle).
  // Pygame keeps that role base through V2 motions, so do not reinterpret V2
  // filenames such as idle01.mtn as V3 per-motion expression instructions.
  if (metadata.runtimeKind === 'v2') {
    return selectBaseExpression(metadata, baseSemantic)
  }

  const supported = new Set(metadata.expressionIds)
  const motionFile = metadata.motionFilesByGroup[group]?.[motionIndex] ?? null
  const exact = motionFile && supportedExpression(expressionCandidatesForTokens(normalizedNameTokens(motionFile)), supported)
  if (exact) return { expression: exact, source: 'motion' }

  const semantic = supportedExpression(SEMANTIC_EXPRESSION_CANDIDATES[group.trim().toLowerCase()] || [], supported)
  if (semantic) return { expression: semantic, source: 'semantic' }

  const groupTokenMatch = supportedExpression(expressionCandidatesForTokens(normalizedNameTokens(group)), supported)
  if (groupTokenMatch) return { expression: groupTokenMatch, source: 'semantic' }

  for (const token of normalizedNameTokens(group)) {
    const tokenSemantic = supportedExpression(SEMANTIC_EXPRESSION_CANDIDATES[token] || [], supported)
    if (tokenSemantic) return { expression: tokenSemantic, source: 'semantic' }
  }

  const idle = supportedExpression(SEMANTIC_EXPRESSION_CANDIDATES.idle, supported)
  if (idle) return { expression: idle, source: 'idle' }
  return { expression: null, source: 'none' }
}

export function selectBaseExpression(
  metadata: Live2DPresentationMetadata,
  baseSemantic: 'idle' | 'serious',
): ExpressionSelection {
  const supported = new Set(metadata.expressionIds)
  const base = supportedExpression(SEMANTIC_EXPRESSION_CANDIDATES[baseSemantic], supported)
  if (base) return { expression: base, source: 'base' }
  const idle = supportedExpression(SEMANTIC_EXPRESSION_CANDIDATES.idle, supported)
  if (idle) return { expression: idle, source: 'idle' }
  return { expression: null, source: 'none' }
}
