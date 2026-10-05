import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { SpeechProcess } from '../src/main/processes/speech-process.js'
import { Speech } from '../src/backend/speech/client.js'
import { Assets } from '../src/backend/resources/assets.js'
import { CharacterCatalog } from '../src/backend/characters/catalog.js'

// 显式运行的本机验收；不访问模型 API，不修改外部资源。
try {
  process.loadEnvFile?.('.env.local')
} catch (error) {
  if (error.code !== 'ENOENT') throw error
}
const root = process.env.DSAKIKO_RESOURCE_ROOT
if (!root) throw new Error('需要 DSAKIKO_RESOURCE_ROOT')
const data = await mkdtemp(join(tmpdir(), 'dsakiko-speech-check-'))
const settings = {
  resourceRoot: root,
  pythonExecutable: process.env.DSAKIKO_PYTHON || 'python3',
  maxConcurrent: 2,
  maxResident: 2,
  device: 'cpu'
}
const host = new SpeechProcess(resolve('python'), data, () => settings)
try {
  const assets = await new Assets(data, () => root).initialize()
  const catalog = new CharacterCatalog(data, assets, () => root)
  const summaries = await catalog.refresh()
  const characters = await Promise.all(
    summaries.map((entry) => catalog.resolveCapabilities(entry.id))
  )
  console.log(
    '资源检查：',
    characters.map((entry) => ({
      id: entry.id,
      model: Boolean(entry.presentation),
      voice: Boolean(entry.voice)
    }))
  )
  const ids = process.argv.slice(2)
  const selected = characters.filter((entry) =>
    ids.length ? ids.includes(entry.id) : entry.id === 'sakiko'
  )
  const speech = new Speech(() => host.connect(), assets)
  const tasks = await Promise.all(
    selected.map(async (character) => {
      if (!character.voice) throw new Error(`${character.id} 缺少声音资源`)
      const id = await speech.submit({
        text: 'こんにちは。',
        language: 'ja',
        voice: character.voice,
        priority: 1
      })
      console.log('已提交：', character.id)
      const result = await speech.waitForResult(id, AbortSignal.timeout(600000))
      console.log('推理结果：', character.id, result)
      if (result.status !== 'succeeded') throw new Error('真实语音推理未成功')
      return result
    })
  )
  console.log(`完成 ${tasks.length} 个真实语音任务。`)
} catch (error) {
  await host.close()
  console.error(await readFile(join(data, 'logs', 'speech.log'), 'utf8').catch(() => ''))
  throw error
} finally {
  await host.close()
  await rm(data, { recursive: true, force: true })
}
