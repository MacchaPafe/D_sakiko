/**
 * 根据角色形态主题色，在 OKLCH 色彩空间下派生与暖纸浅色界面和谐且满足 WCAG 对比度的局部语义色。
 * 算法对齐 Python 版 `GPT_SoVITS/ui_main/theme.py`，并补齐 Electron 舞台深底字幕和角色色标语义。
 */

const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/
const WARM_PAPER_BACKGROUND = '#FFFDFA'
const STAGE_DARK_BACKDROP = '#2B2421'
const MIN_PRIMARY_CONTRAST = 4.5
const MIN_SPEAKER_CONTRAST = 6.5
const CONTRAST_SEARCH_MARGIN = 0.05
const PALETTE_CACHE_LIMIT = 256

/**
 * @typedef {object} CharacterThemePalette
 * @property {string} seed 规范化后的大写 `#RRGGBB` 角色原色。
 * @property {string} primary 浅底强调文字、图标及发送按钮底色（与浅底及暖白前景均满足 WCAG AA 对比度）。
 * @property {string} primaryForeground 实心强调按钮上的前景文字/图标色。
 * @property {string} soft 选中行、激活态与用户消息气泡使用的柔和浅底色。
 * @property {string} border 选中卡片与控件的柔和边框色。
 * @property {string} speaker 舞台深色遮罩上使用的柔和浅色说话人名称色。
 * @property {string} badge 侧边栏与列表项上用于识别角色的克制色标色。
 */

/** @type {Map<string, CharacterThemePalette>} */
const paletteCache = new Map()

/**
 * 校验并规范化 `#RRGGBB` 颜色字符串。
 * @param {string | null | undefined} seed 原始颜色字符串。
 * @returns {string | null} 规范化后的大写 `#RRGGBB`，无效时返回 null。
 */
export function normalizeThemeColor(seed) {
  if (typeof seed !== 'string') return null
  const trimmed = seed.trim()
  if (!HEX_COLOR_PATTERN.test(trimmed)) return null
  return trimmed.toUpperCase()
}

function srgbChannelToLinear(channel) {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

function linearChannelToSrgb(channel) {
  if (channel <= 0.0031308) return 12.92 * channel
  return 1.055 * channel ** (1 / 2.4) - 0.055
}

function hexToRgb(hex) {
  return [
    parseInt(hex.slice(1, 3), 16) / 255,
    parseInt(hex.slice(3, 5), 16) / 255,
    parseInt(hex.slice(5, 7), 16) / 255
  ]
}

function rgbToHex(r, g, b) {
  const toByte = (value) =>
    Math.round(Math.min(1, Math.max(0, value)) * 255)
      .toString(16)
      .padStart(2, '0')
  return `#${toByte(r)}${toByte(g)}${toByte(b)}`.toUpperCase()
}

function relativeLuminance(hex) {
  const [r, g, b] = hexToRgb(hex)
  return (
    0.2126 * srgbChannelToLinear(r) +
    0.7152 * srgbChannelToLinear(g) +
    0.0722 * srgbChannelToLinear(b)
  )
}

/**
 * 计算两种不透明 `#RRGGBB` 颜色的 WCAG 2.1 对比度。
 * @param {string} first 第一种颜色。
 * @param {string} second 第二种颜色。
 * @returns {number} 对比度比值（1 到 21）。
 */
export function contrastRatio(first, second) {
  const l1 = relativeLuminance(first)
  const l2 = relativeLuminance(second)
  const lighter = Math.max(l1, l2)
  const darker = Math.min(l1, l2)
  return (lighter + 0.05) / (darker + 0.05)
}

function hexToOklch(hex) {
  const [sr, sg, sb] = hexToRgb(hex)
  const r = srgbChannelToLinear(sr)
  const g = srgbChannelToLinear(sg)
  const b = srgbChannelToLinear(sb)

  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)

  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const bLab = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s

  const C = Math.hypot(a, bLab)
  let h = (Math.atan2(bLab, a) * 180) / Math.PI
  if (h < 0) h += 360
  return { L, C, h }
}

function oklchToLinearRgb(L, C, h) {
  const rad = (h * Math.PI) / 180
  const a = C * Math.cos(rad)
  const b = C * Math.sin(rad)

  const l_ = L + 0.3963377774 * a + 0.2158037573 * b
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b
  const s_ = L - 0.0894841775 * a - 1.291485548 * b

  const l = l_ * l_ * l_
  const m = m_ * m_ * m_
  const s = s_ * s_ * s_

  return [
    +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s
  ]
}

function isInSrgbGamut(linearRgb) {
  const eps = 1e-6
  return (
    linearRgb[0] >= -eps &&
    linearRgb[0] <= 1 + eps &&
    linearRgb[1] >= -eps &&
    linearRgb[1] <= 1 + eps &&
    linearRgb[2] >= -eps &&
    linearRgb[2] <= 1 + eps
  )
}

/**
 * 保持明度与色相不变，在 sRGB 色域内二分收缩色度（等价于 oklch-chroma 色域映射）。
 */
function oklchToHex(lightness, chroma, hue) {
  const L = Math.min(1, Math.max(0, lightness))
  const C = Math.max(0, chroma)
  const direct = oklchToLinearRgb(L, C, hue)
  if (isInSrgbGamut(direct)) {
    return rgbToHex(
      linearChannelToSrgb(direct[0]),
      linearChannelToSrgb(direct[1]),
      linearChannelToSrgb(direct[2])
    )
  }

  let low = 0
  let high = C
  let best = oklchToLinearRgb(L, 0, hue)
  for (let i = 0; i < 24; i += 1) {
    const mid = (low + high) / 2
    const candidate = oklchToLinearRgb(L, mid, hue)
    if (isInSrgbGamut(candidate)) {
      low = mid
      best = candidate
    } else {
      high = mid
    }
  }
  return rgbToHex(
    linearChannelToSrgb(best[0]),
    linearChannelToSrgb(best[1]),
    linearChannelToSrgb(best[2])
  )
}

/**
 * 在指定色相和色度下，寻找同时满足多组浅色背景对比度要求的最亮文字/主色。
 */
function findReadableDarkTone(hue, chroma, backgrounds, minimumContrast) {
  const target = minimumContrast + CONTRAST_SEARCH_MARGIN
  let low = 0
  let high = 1
  let best = oklchToHex(0, chroma, hue)
  for (let i = 0; i < 28; i += 1) {
    const mid = (low + high) / 2
    const candidate = oklchToHex(mid, chroma, hue)
    const passesAll = backgrounds.every((bg) => contrastRatio(candidate, bg) >= target)
    if (passesAll) {
      low = mid
      best = candidate
    } else {
      high = mid
    }
  }
  return best
}

/**
 * 在指定色相和色度下，为深色舞台遮罩寻找柔和且高可读性的浅色说话人名称色。
 */
function findReadableLightTone(hue, chroma, background, minimumContrast, minLightness) {
  const target = minimumContrast + CONTRAST_SEARCH_MARGIN
  let low = minLightness
  let high = 0.96
  let best = oklchToHex(high, chroma, hue)
  for (let i = 0; i < 28; i += 1) {
    const mid = (low + high) / 2
    const candidate = oklchToHex(mid, chroma, hue)
    if (contrastRatio(candidate, background) >= target) {
      high = mid
      best = candidate
    } else {
      low = mid
    }
  }
  return best
}

/**
 * 根据角色主题色派生局部语义色板，无效或空值返回 null。
 * @param {string | null | undefined} seed 角色主题色（`#RRGGBB`）。
 * @returns {CharacterThemePalette | null} 派生色板，未设置或非法时返回 null。
 */
export function deriveCharacterTheme(seed) {
  const normalized = normalizeThemeColor(seed)
  if (!normalized) return null
  const cached = paletteCache.get(normalized)
  if (cached) return cached

  const oklch = hexToOklch(normalized)
  const isAchromatic = Number.isNaN(oklch.h) || oklch.C < 0.01
  const hue = isAchromatic ? 0 : oklch.h
  const chroma = isAchromatic ? 0 : oklch.C

  const soft = oklchToHex(0.935, Math.min(chroma * 0.34, 0.055), hue)
  const border = oklchToHex(0.855, Math.min(chroma * 0.24, 0.04), hue)

  // 同时对选中浅底和暖纸白底校验对比度，既保证浅底文字清晰，也确保发送按钮的暖白图标始终满足 AA 对比度。
  const primary = findReadableDarkTone(
    hue,
    Math.min(chroma * 0.78, 0.16),
    [soft, WARM_PAPER_BACKGROUND],
    MIN_PRIMARY_CONTRAST
  )

  const speaker = findReadableLightTone(
    hue,
    Math.min(chroma * 0.52, 0.105),
    STAGE_DARK_BACKDROP,
    MIN_SPEAKER_CONTRAST,
    0.82
  )

  // 侧边栏色标保留较高色相辨识度，但避免极浅黄/极深暗色在暖纸卡片上发虚或突兀。
  const badgeLightness = Math.min(0.76, Math.max(0.42, oklch.L))
  const badge = oklchToHex(badgeLightness, Math.min(chroma * 0.9, 0.18), hue)

  const palette = {
    seed: normalized,
    primary,
    primaryForeground: WARM_PAPER_BACKGROUND,
    soft,
    border,
    speaker,
    badge
  }

  if (paletteCache.size >= PALETTE_CACHE_LIMIT) {
    const oldestKey = paletteCache.keys().next().value
    if (oldestKey !== undefined) paletteCache.delete(oldestKey)
  }
  paletteCache.set(normalized, palette)
  return palette
}

/**
 * 从角色摘要中解析指定形态的主题色，缺省时回退到角色默认主题色。
 * @param {{ themeColor?: string | null, formThemeColors?: Record<string, string | null> } | null | undefined} role 角色摘要对象。
 * @param {string | null | undefined} formId 当前激活形态 ID。
 * @returns {string | null} 规范化后的主题色，未设置时返回 null。
 */
export function resolveCharacterThemeColor(role, formId) {
  if (!role) return null
  if (formId && role.formThemeColors && Object.hasOwn(role.formThemeColors, formId)) {
    return normalizeThemeColor(role.formThemeColors[formId])
  }
  return normalizeThemeColor(role.themeColor)
}

/**
 * 生成单人对话舞台区域的局部 CSS 变量覆盖对象。
 * @param {string | null | undefined} seed 当前角色当前形态的主题色。
 * @returns {Record<string, string> | undefined} CSS 变量表；未设主题色时返回 undefined 以沿用全局默认配色。
 */
export function characterStageThemeVars(seed) {
  const palette = deriveCharacterTheme(seed)
  if (!palette) return undefined
  return {
    '--primary': palette.primary,
    '--primary-foreground': palette.primaryForeground,
    '--secondary': palette.soft,
    '--secondary-foreground': palette.primary,
    '--accent': palette.soft,
    '--accent-foreground': palette.primary,
    '--selected-border': palette.border,
    '--ring': palette.primary,
    '--speaker': palette.speaker,
    '--subtitle-accent': palette.speaker,
    '--role-badge': palette.badge
  }
}

/**
 * 生成侧边栏单个角色分组的局部 CSS 变量覆盖对象。
 * @param {string | null | undefined} seed 角色主题色。
 * @returns {Record<string, string> | undefined} CSS 变量表；未设主题色时返回 undefined。
 */
export function characterSidebarGroupVars(seed) {
  const palette = deriveCharacterTheme(seed)
  if (!palette) return undefined
  return {
    '--primary': palette.primary,
    '--primary-foreground': palette.primaryForeground,
    '--secondary': palette.soft,
    '--secondary-foreground': palette.primary,
    '--accent': palette.soft,
    '--accent-foreground': palette.primary,
    '--selected-border': palette.border,
    '--ring': palette.primary,
    '--role-badge': palette.badge
  }
}

/**
 * 生成角色管理页预览区域所需的局部 CSS 变量覆盖对象。
 * @param {string | null | undefined} seed 正在编辑的形态主题色。
 * @returns {Record<string, string> | undefined} CSS 变量表；未设主题色时返回 undefined。
 */
export function characterPreviewVars(seed) {
  return characterStageThemeVars(seed)
}
