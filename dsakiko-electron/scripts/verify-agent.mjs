import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Settings } from '../src/backend/settings/settings.js'
import { Assets } from '../src/backend/resources/assets.js'
import { CharacterCatalog } from '../src/backend/characters/catalog.js'
import { Agent } from '../src/backend/llm/agent.js'

// 本命令显式调用旧配置选中的真实模型服务，会消耗该服务额度。
try {
  process.loadEnvFile?.('.env.local')
} catch (error) {
  if (error.code !== 'ENOENT') throw error
}
const root = process.env.DSAKIKO_RESOURCE_ROOT
if (!root) throw new Error('需要 DSAKIKO_RESOURCE_ROOT')
const data = await mkdtemp(join(tmpdir(), 'dsakiko-agent-check-'))
try {
  const settings = await new Settings(data, { resourceRoot: root }).initialize()
  await settings.importLegacy()
  const assets = await new Assets(data, () => root).initialize()
  const catalog = new CharacterCatalog(data, assets, () => root)
  await catalog.refresh()
  const ids = process.argv.slice(2)
  if (!ids.length) ids.push('sakiko')
  const characters = await Promise.all(ids.map((id) => catalog.resolveCapabilities(id)))
  let tools = 0
  const agent = new Agent(() => settings.get())
  const result = await agent.run(
    {
      mode: ids.length === 2 ? 'scripted-dialogue' : 'single-character',
      characters: characters.map(({ id, displayName, description, performances }) => ({
        id,
        displayName,
        description,
        performances
      })),
      userPersona: {
        displayName: '音乐社团的新朋友',
        description: '用户喜欢钢琴，正在测试一段简短对话。'
      },
      history: [],
      model: { model: settings.get().model, temperature: 0.7, contextTokenBudget: 16000 },
      input: {
        message: { text: '请先使用时间工具查看现在的时间，然后每位角色只用一句简短日语问候我。' }
      }
    },
    {
      onIntermediate: async () => {},
      onToolActivity: async (record) => {
        if (record.status === 'succeeded') tools += 1
      }
    },
    AbortSignal.timeout(240000)
  )
  console.log({
    status: result.status,
    tools,
    lines: result.lines?.map(({ speakerId, text }) => ({ speakerId, text }))
  })
  if (
    result.status !== 'completed' ||
    !tools ||
    ids.some((id) => !result.lines.some((line) => line.speakerId === id))
  )
    throw new Error('真实 Agent Loop 验收未通过')
} finally {
  await rm(data, { recursive: true, force: true })
}
