import { z } from 'zod'
import { SerialQueue, readJson, requireValue, writeJson } from '../backend/core/support.js'

const volume = z.number().min(0).max(1)
const id = z.string().min(1).max(300)
const fields = {
  playback: z.strictObject({ volume, bgmVolume: volume }),
  historySize: z.strictObject({
    width: z.number().positive(),
    height: z.number().positive()
  }),
  navigation: z.strictObject({
    characterId: id.nullable(),
    chatId: id.nullable(),
    tab: z.enum(['single-character', 'scripted-dialogue'])
  }),
  recentModel: z.strictObject({ connectionId: id, modelId: id }),
  outfitVersion: z.enum(['v2', 'v3'])
}

/**
 * 桌面偏好归当前安装持有。并发的音量和导航更新由宿主串行合并。
 * @param {string} file 应用数据目录内的界面偏好文件。
 * @returns {{get: () => Promise<object>, set: (key: string, value: unknown) => Promise<void>, flush: () => Promise<void>}} 读取、单项保存和退出前等待写入的接口。
 */
export function createUiPreferences(file) {
  const queue = new SerialQueue()
  const schema = z.strictObject(fields).partial()
  async function read() {
    return schema.parse(await readJson(file, {}))
  }
  return {
    get: () => queue.run('preferences', read),
    set(key, value) {
      requireValue(Object.hasOwn(fields, key), 'invalid_preference')
      const parsed = fields[key].parse(value)
      return queue.run('preferences', async () => {
        await writeJson(file, { ...(await read()), [key]: parsed })
      })
    },
    flush: () => queue.run('preferences', () => {})
  }
}
