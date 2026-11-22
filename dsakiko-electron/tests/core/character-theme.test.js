import { describe, expect, it } from 'vitest'
import bangDreamCharacters from '../../src/backend/characters/bang-dream.json' with { type: 'json' }
import {
  characterSidebarGroupVars,
  characterStageThemeVars,
  contrastRatio,
  deriveCharacterTheme,
  normalizeThemeColor,
  resolveCharacterThemeColor
} from '../../src/renderer/src/styles/character-theme.js'

const HEX_PATTERN = /^#[0-9A-F]{6}$/

describe('character-theme', () => {
  it('规范化合法颜色并忽略非法或空值', () => {
    expect(normalizeThemeColor(' #7799cc ')).toBe('#7799CC')
    expect(normalizeThemeColor(null)).toBeNull()
    expect(normalizeThemeColor(undefined)).toBeNull()
    expect(normalizeThemeColor('')).toBeNull()
    expect(normalizeThemeColor('#fff')).toBeNull()
    expect(normalizeThemeColor('rgb(1,2,3)')).toBeNull()
    expect(deriveCharacterTheme(null)).toBeNull()
    expect(characterStageThemeVars(null)).toBeUndefined()
    expect(characterSidebarGroupVars('invalid')).toBeUndefined()
  })

  it('所有 BanG Dream! 预设角色色均满足浅底、按钮前景与深底字幕对比度要求', () => {
    expect(bangDreamCharacters.length).toBeGreaterThan(0)
    for (const character of bangDreamCharacters) {
      const palette = deriveCharacterTheme(character.themeColor)
      expect(palette, `${character.id} (${character.themeColor}) 应生成色板`).not.toBeNull()
      for (const key of [
        'seed',
        'primary',
        'primaryForeground',
        'soft',
        'border',
        'speaker',
        'badge'
      ]) {
        expect(palette[key], `${character.id}.${key}`).toMatch(HEX_PATTERN)
      }
      expect(
        contrastRatio(palette.primary, palette.soft),
        `${character.id} primary/soft 对比度`
      ).toBeGreaterThanOrEqual(4.5)
      expect(
        contrastRatio(palette.primary, palette.primaryForeground),
        `${character.id} primary/foreground 对比度`
      ).toBeGreaterThanOrEqual(4.5)
      expect(
        contrastRatio(palette.speaker, '#2B2421'),
        `${character.id} speaker/stage 对比度`
      ).toBeGreaterThanOrEqual(6.5)
    }
  })

  it('极端自定义颜色（纯白、纯黑、高饱和荧光色与中性灰）都能稳定派生并满足对比度', () => {
    const extremeSeeds = [
      '#FFFFFF',
      '#000000',
      '#FFFF00',
      '#00FF00',
      '#00FFFF',
      '#FF00FF',
      '#BBBBBB'
    ]
    for (const seed of extremeSeeds) {
      const palette = deriveCharacterTheme(seed)
      expect(palette).not.toBeNull()
      expect(contrastRatio(palette.primary, palette.soft)).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(palette.primary, palette.primaryForeground)).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(palette.speaker, '#2B2421')).toBeGreaterThanOrEqual(6.5)
    }
  })

  it('按人格优先解析主题色并正确输出舞台与侧栏变量', () => {
    const role = {
      themeColor: '#7799CC',
      formThemeColors: {
        black: '#335566',
        white: null
      }
    }
    expect(resolveCharacterThemeColor(role, 'black')).toBe('#335566')
    expect(resolveCharacterThemeColor(role, 'white')).toBeNull()
    expect(resolveCharacterThemeColor(role, 'unknown')).toBe('#7799CC')
    expect(resolveCharacterThemeColor(role, null)).toBe('#7799CC')

    const stageVars = characterStageThemeVars('#7799CC')
    expect(stageVars['--primary']).toMatch(HEX_PATTERN)
    expect(stageVars['--primary-foreground']).toBe('#FFFDFA')
    expect(stageVars['--secondary']).toMatch(HEX_PATTERN)
    expect(stageVars['--speaker']).toMatch(HEX_PATTERN)
    expect(stageVars['--subtitle-accent']).toBe(stageVars['--speaker'])

    const sidebarVars = characterSidebarGroupVars('#7799CC')
    expect(sidebarVars['--role-badge']).toMatch(HEX_PATTERN)
    expect(sidebarVars['--speaker']).toBeUndefined()
  })
})
