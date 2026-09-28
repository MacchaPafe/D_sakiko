import { describe, expect, it } from 'vitest'
import fixtures from '../../../../GPT_SoVITS/test/fixtures/performance_cases.json'
import { resolvePerformance } from './cuePolicy'

describe('shared performance contract', () => {
  for (const sample of fixtures.cases) {
    it(sample.name, () => {
      const result = resolvePerformance({ ...fixtures.catalog, version: sample.version || 'v3' },
        sample.selection, sample.emotion, sample.direction || 'C', sample.current)
      const actual = result.legacy_group ? { legacy_group: result.legacy_group } : {
        motion_file: result.state.motion_file,
        expression: result.state.expression,
        change_motion: result.change_motion,
        change_expression: result.change_expression,
      }
      expect(actual).toEqual(sample.expected)
    })
  }
})
