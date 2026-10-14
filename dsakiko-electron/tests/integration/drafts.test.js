import { expect, test } from 'vitest'
import { Drafts } from '../../src/renderer/src/features/chat/drafts.js'

test('发送确认保护新草稿，同一草稿重试复用编号', () => {
  const drafts = new Drafts()
  drafts.update('a', '第一条')
  const first = drafts.prepare('a')
  expect(drafts.prepare('a').requestId).toBe(first.requestId)
  drafts.update('a', '第二条')
  drafts.acknowledge('a', first.requestId)
  expect(drafts.get('a')).toBe('第二条')
})
