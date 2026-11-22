import { expect, test, vi } from 'vitest'
import { HomeFlow, selectedModel } from '../../src/renderer/src/features/home/home-flow.js'
import { historyRows } from '../../src/renderer/src/features/home/history.js'
function fixture() {
  const client = {
    characters: { list: async () => [{ id: 'sakiko' }, { id: 'anon' }], listPersonas: async () => [] },
    conversations: {
      listChats: async () => [],
      observe: vi.fn(() => () => {}),
      createChatWithTurn: vi.fn()
    },
    settings: { get: async () => ({}), listConnections: async () => [] }
  }
  const flow = new HomeFlow(client, { storage: null, randomId: () => 'stable-intent' })
  flow.newChat('sakiko')
  return { flow, client }
}
test('丢失首轮响应后重试同一意图，双击不重复发送，清空后的新对话不复用正式聊天', async () => {
  const { flow, client } = fixture()
  const onTurnAccepted = vi.fn()
  flow.setDraft('你好')
  client.conversations.createChatWithTurn
    .mockRejectedValueOnce(Error('连接中断'))
    .mockResolvedValue({ chatId: 'first' })
  await flow.send(onTurnAccepted)
  expect(onTurnAccepted).not.toHaveBeenCalled()
  expect(flow.state.draft).toBe('你好')
  await Promise.all([flow.send(onTurnAccepted), flow.send(onTurnAccepted)])
  expect(client.conversations.createChatWithTurn).toHaveBeenCalledTimes(2)
  expect(client.conversations.createChatWithTurn.mock.calls[0]).toEqual(
    client.conversations.createChatWithTurn.mock.calls[1]
  )
  expect(flow.state.target.chatId).toBe('first')
  expect(onTurnAccepted).toHaveBeenCalledExactlyOnceWith('first')
  expect(flow.state.draft).toBe('')
  flow.newChat('sakiko')
  expect(flow.input().chatId).toBeUndefined()
})

test('新对话临时服装随首轮提交，重开时恢复默认，正式对话只更新当前人格的覆盖', async () => {
  const { flow, client } = fixture()
  const outfit = { id: 'stage', name: '演出服', presentation: { model: { id: 'asset' } } }
  await flow.chooseOutfit('form', outfit, 'default')
  expect(flow.state.draftOutfit).toEqual({ formId: 'form', model: outfit })
  flow.newChat('sakiko')
  expect(flow.state.draftOutfit).toBeNull()

  await flow.chooseOutfit('form', outfit, 'default')
  flow.setDraft('使用当前服装')
  client.conversations.createChatWithTurn
    .mockRejectedValueOnce(Error('连接中断'))
    .mockResolvedValue({ chatId: 'first' })
  await flow.send()
  await flow.send()
  const [first, retry] = client.conversations.createChatWithTurn.mock.calls
  expect(first).toEqual(retry)
  expect(first[0].settings.characters).toEqual({
    sakiko: { formId: 'form', modelOverrides: { form: 'stage' } }
  })
  expect(flow.state.draftOutfit).toBeNull()

  client.conversations.patchSettings = vi.fn()
  await flow.chooseOutfit('form', { id: 'default' }, 'default')
  expect(client.conversations.patchSettings).toHaveBeenCalledWith('first', {
    characters: { sakiko: { formId: 'form', modelOverrides: { form: 'default' } } }
  })
  flow.newChat('sakiko')
  expect(flow.state.draftOutfit).toBeNull()
})
test('首轮可以直接选择无模型的人格，草稿保留其他人格试穿过的服装', async () => {
  const { flow, client } = fixture()
  await flow.chooseOutfit('black', { id: 'costume' }, 'sakiko')
  await flow.chooseForm('white')
  expect(flow.state.draftOutfit).toEqual({ formId: 'white', model: null })
  await flow.chooseForm('black')
  expect(flow.state.draftOutfit.model.id).toBe('costume')
  await flow.chooseForm('white')
  flow.setDraft('开始')
  client.conversations.createChatWithTurn.mockResolvedValue({ chatId: 'white-chat' })
  await flow.send()
  const [options] = client.conversations.createChatWithTurn.mock.calls[0]
  expect(options.settings.characters.sakiko).toEqual({
    formId: 'white',
    modelOverrides: { black: 'costume' }
  })
})

test('每个角色草稿保留选择，删除身份只清选择，首发重试固定身份', async () => {
  const { flow, client } = fixture()
  client.characters.listPersonas = async () => [{ id: 'me' }]
  await flow.refreshPersonas()
  flow.choosePersona('me')
  flow.setDraft('在这里等我')
  flow.newChat('anon')
  expect(flow.input().userPersonaId).toBeUndefined()
  flow.choosePersona('deleted')
  flow.setDraft('另一份草稿')
  await flow.refreshPersonas()
  expect(flow.input().userPersonaId).toBeNull()
  expect(flow.state.draft).toBe('另一份草稿')
  flow.newChat('sakiko')
  expect(flow.input().userPersonaId).toBe('me')
  client.conversations.createChatWithTurn.mockRejectedValueOnce(Error('中断')).mockResolvedValue({ chatId: 'first' })
  await flow.send()
  await flow.send()
  const [first, retry] = client.conversations.createChatWithTurn.mock.calls
  expect(retry).toEqual(first)
  expect(first[0].userPersonaId).toBe('me')
  flow.choosePersona('other')
  expect(flow.input().userPersonaId).toBeUndefined()
  flow.newChat('sakiko')
  expect(flow.input().userPersonaId).toBeUndefined()
})

test('切换角色和继续编辑时，迟到受理只交接原草稿，不覆盖当前输入', async () => {
  const { flow, client } = fixture()
  let finish
  client.conversations.createChatWithTurn.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  flow.setDraft('发给祥子')
  const sending = flow.send()
  flow.setDraft('下一句话')
  flow.newChat('anon')
  flow.setDraft('发给爱音')
  finish({ chatId: 'sakiko-chat' })
  await sending
  expect(flow.state.target.characterId).toBe('anon')
  expect(flow.state.draft).toBe('发给爱音')
  flow.selectChat({ id: 'sakiko-chat', characterIds: ['sakiko'], mode: 'single-character' })
  expect(flow.state.target.chatId).toBe('sakiko-chat')
  expect(flow.state.draft).toBe('下一句话')
})
test('存档投影使用正式 kind 字段，工具和失败不混入角色台词', () => {
  const rows = historyRows({
    chat: {
      turns: [
        {
          id: 't',
          messages: [
            { id: 'u', kind: 'user', source: { kind: 'manual' }, content: { text: '你好' } },
            {
              id: 'a',
              kind: 'assistant',
              parts: [
                { kind: 'dialogue', lines: [{ id: 'line', speakerId: 'sakiko', text: '欢迎' }] },
                { kind: 'tool-call', name: 'weather', callId: 'weather-call', arguments: {} }
              ]
            },
            {
              id: 'tool',
              kind: 'tool-result',
              assistantMessageId: 'a',
              callId: 'weather-call',
              outcome: { status: 'failed', problem: { message: '无法连接' } }
            }
          ],
          problem: { message: '生成失败' }
        }
      ]
    }
  })
  expect(rows.map((row) => row.kind)).toEqual(['user', 'assistant', 'tools', 'system'])
  expect(rows.map((row) => row.text)).toContain('欢迎')
  expect(rows[2].tools).toHaveLength(1)
  expect(rows[2].tools[0].outcome.problem.message).toBe('无法连接')
})

test('开发模式清理后重新挂载恢复交互，迟到的首次挂载不重复订阅', async () => {
  const { flow, client } = fixture()
  let releaseFirst
  client.characters.list = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseFirst = resolve
        })
    )
    .mockResolvedValue([{ id: 'sakiko' }, { id: 'anon' }])
  client.settings.observe = vi.fn(() => vi.fn())
  client.notices = { observe: vi.fn(() => vi.fn()) }
  client.observe = vi.fn(() => vi.fn())
  const first = flow.mount()
  flow.dispose()
  await flow.mount()
  expect(flow.state.loading).toBe(false)
  flow.newChat('anon')
  flow.setDraft('开发模式仍可输入')
  expect(flow.state.target.characterId).toBe('anon')
  expect(flow.state.draft).toBe('开发模式仍可输入')
  releaseFirst([{ id: 'stale' }])
  await first
  expect(flow.state.target.characterId).toBe('anon')
  expect(flow.state.characters.map((role) => role.id)).toEqual(['sakiko', 'anon'])
  expect(client.settings.observe).toHaveBeenCalledTimes(1)
  expect(client.notices.observe).toHaveBeenCalledTimes(1)
  flow.dispose()
})

test('配置连接后未显式选择模型时默认选中第一个可用模型，并在发送时写入新对话', async () => {
  const { flow, client } = fixture()
  client.settings.listConnections = async () => [
    { id: 'empty', name: '空连接', models: [] },
    {
      id: 'deepseek',
      name: 'DeepSeek',
      models: [{ id: 'deepseek-chat' }, { id: 'deepseek-reasoner' }]
    }
  ]
  client.settings.observe = vi.fn(() => vi.fn())
  client.notices = { observe: vi.fn(() => vi.fn()) }
  client.observe = vi.fn(() => vi.fn())
  client.conversations.createChatWithTurn.mockResolvedValue({ chatId: 'auto-model-chat' })

  await flow.mount()
  expect(selectedModel(flow.state)).toEqual({
    connectionId: 'deepseek',
    modelId: 'deepseek-chat'
  })

  flow.setDraft('使用默认模型发送')
  await flow.send()
  expect(client.conversations.createChatWithTurn).toHaveBeenCalledWith(
    {
      title: '使用默认模型发送',
      characterIds: ['sakiko'],
      mode: 'single-character',
      settings: { generation: { connectionId: 'deepseek', modelId: 'deepseek-chat' } }
    },
    { text: '使用默认模型发送', attachments: [] },
    expect.objectContaining({ requestId: 'stable-intent' })
  )
  flow.dispose()
})

test('在对话与小剧场页签之间切换时恢复各页签上次选中的对话', async () => {
  const { flow, client } = fixture()
  const chats = [
    { id: 'chat-sakiko', characterIds: ['sakiko'], mode: 'single-character' },
    { id: 'chat-theatre', characterIds: ['sakiko', 'anon'], mode: 'scripted-dialogue' }
  ]
  client.conversations.listChats = async () => chats
  client.settings.observe = vi.fn(() => vi.fn())
  client.notices = { observe: vi.fn(() => vi.fn()) }
  client.observe = vi.fn(() => vi.fn())

  await flow.mount()
  flow.selectChat(chats[0])
  expect(flow.state.target).toEqual({
    characterId: 'sakiko',
    chatId: 'chat-sakiko',
    tab: 'single-character'
  })

  flow.setTab('scripted-dialogue')
  expect(flow.state.target).toEqual({
    characterId: 'sakiko',
    chatId: null,
    tab: 'scripted-dialogue'
  })

  flow.setTab('single-character')
  expect(flow.state.target).toEqual({
    characterId: 'sakiko',
    chatId: 'chat-sakiko',
    tab: 'single-character'
  })

  flow.setTab('scripted-dialogue')
  flow.selectChat(chats[1])
  flow.setTab('single-character')
  expect(flow.state.target.chatId).toBe('chat-sakiko')
  flow.setTab('scripted-dialogue')
  expect(flow.state.target.chatId).toBe('chat-theatre')
  flow.dispose()
})
