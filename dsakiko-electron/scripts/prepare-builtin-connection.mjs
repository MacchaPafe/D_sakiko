import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

/** 发布时从受保护环境变量生成内置连接，不输出密钥、不把凭据提交到仓库。 */
async function prepare() {
  const secret = process.env.DSAKIKO_UP_API_KEY
  if (!secret?.trim()) throw new Error('缺少 DSAKIKO_UP_API_KEY，尚未生成内置 UP 连接。')
  const directory = fileURLToPath(new URL('../resources/', import.meta.url))
  await mkdir(directory, { recursive: true })
  await writeFile(
    path.join(directory, 'builtin-connection.json'),
    JSON.stringify(
      { secret, modelId: 'deepseek-flash', baseUrl: 'https://api.deepseek.com/v1' },
      null,
      2
    ) + '\n',
    { mode: 0o600 }
  )
  process.stdout.write('已准备内置连接发布配置。\n')
}
await prepare()
