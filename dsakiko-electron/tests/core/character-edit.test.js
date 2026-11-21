import { expect, test } from 'vitest'
import {
  newCharacter,
  mergeCharacterEdit
} from '../../src/renderer/src/features/characters/character-edit.js'
import {
  applyVoiceChanges,
  voiceBaseline,
  voiceChanges
} from '../../src/backend/setup/voice-update.js'
import {
  configuredVoice,
  missingVoiceMaterials,
  setReplyVoice
} from '../../src/shared/reply-voice.js'

function sampleVoice() {
  return {
    gptWeights: { id: 'gpt' },
    sovitsWeights: { id: 'sovits' },
    defaultReference: { audio: { id: 'default' }, text: '台词', language: 'ja' },
    emotions: { sadness: { audio: { id: 'sad' }, text: '悲伤', language: 'ja' } }
  }
}
test('角色改名跟随共用显示名，编辑保留同时安装的服装', () => {
  const base = newCharacter()
  base.name = base.forms[0].displayName = '原名'
  base.revision = 1
  base.forms.push({ ...structuredClone(base.forms[0]), id: 'white', displayName: '独立称呼' })
  const edited = structuredClone(base)
  edited.name = '新名'
  edited.forms[1].description = '独立人设'
  const current = structuredClone(base)
  current.forms[1].models.push({ id: 'new-outfit' })
  current.revision++
  const result = mergeCharacterEdit(base, edited, current)
  expect(result.forms.map((form) => form.displayName)).toEqual(['新名', '独立称呼'])
  expect(result.forms[1].models).toEqual([{ id: 'new-outfit' }])
  current.forms[1].description = '另一份人设'
  expect(() => mergeCharacterEdit(base, edited, current)).toThrow('同一项资料')
})
test('参考音频、文本和语言成组更新，保留本地修改和下载期间的新编辑', () => {
  const old = sampleVoice()
  const form = { voice: structuredClone(old) }
  form.voice.defaultReference.text = '我的台词'
  const incoming = sampleVoice()
  incoming.gptWeights = { id: 'new-gpt' }
  incoming.sovitsWeights = { id: 'new-sovits' }
  incoming.defaultReference = { audio: { id: 'new-default' }, text: '新版台词', language: 'en' }
  incoming.emotions = {}
  const changes = voiceChanges(form, incoming, voiceBaseline(old))
  expect(changes.find((change) => change.key === 'defaultReference').modified).toBe(true)
  expect(changes.find((change) => change.key === 'sadness')).toMatchObject({
    modified: false,
    removed: true
  })
  form.voice.sovitsWeights = { id: 'during-download' }
  applyVoiceChanges(
    form,
    incoming,
    changes.filter((change) => !change.modified),
    'voice.anon'
  )
  expect(form.voice.defaultReference).toEqual({ ...old.defaultReference, text: '我的台词' })
  expect(form.voice.gptWeights).toEqual(incoming.gptWeights)
  expect(form.voice.sovitsWeights.id).toBe('during-download')
  expect(form.voice.emotions).toEqual({})
  expect(form.voiceBaseline).toEqual(voiceBaseline(incoming))
})
test('语音允许分次配置和停用，情绪各自持有参考材料', () => {
  const form = {}
  setReplyVoice(form, { gptWeights: { id: 'partial' } }, false)
  expect(configuredVoice(form).gptWeights.id).toBe('partial')
  expect(missingVoiceMaterials(configuredVoice(form))).toContain('默认参考文本')
  const complete = sampleVoice()
  setReplyVoice(form, complete, true)
  setReplyVoice(form, complete, false)
  expect(form.voice).toBeNull()
  expect(configuredVoice(form)).toEqual(complete)
})
