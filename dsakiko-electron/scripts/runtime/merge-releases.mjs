import path from 'node:path'
import { createReadStream } from 'node:fs'
import { cp, mkdir, readFile, rename, stat, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { parseArgs } from 'node:util'
import { runtimePackages } from '../../src/backend/setup/catalog.js'

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { output: { type: 'string', default: 'dist/runtimes' } }
})
if (!positionals.length)
  throw new Error(
    '用法：node scripts/runtime/merge-releases.mjs Mac/release.json Windows/release.json --output dist/runtimes'
  )
const packages = new Map()
const files = new Map()

// 先验证所有输入和归档，冲突时不产生半份发布声明。
for (const input of positionals) {
  const manifestFile = path.resolve(input)
  const manifest = JSON.parse(await readFile(manifestFile, 'utf8'))
  runtimePackages(manifest)
  for (const item of manifest.packages) {
    const key = `${item.kind}.${item.target}`
    const previous = packages.get(key)
    if (previous && JSON.stringify(previous) !== JSON.stringify(item))
      throw new Error(`同一目标包含不同构建，请明确保留其中一个：${key}`)
    packages.set(key, item)
    for (const file of item.files) {
      const existing = files.get(file.path)
      if (existing && existing.sha256 !== file.sha256) throw new Error(`归档路径冲突：${file.path}`)
      const source = path.join(path.dirname(manifestFile), file.path)
      const hash = createHash('sha256')
      for await (const chunk of createReadStream(source)) hash.update(chunk)
      if ((await stat(source)).size !== file.sizeBytes || hash.digest('hex') !== file.sha256)
        throw new Error(`归档与声明不一致：${source}`)
      files.set(file.path, { ...file, source })
    }
  }
}
const destination = path.resolve(values.output)
await mkdir(destination, { recursive: true })
for (const [name, file] of files) {
  const target = path.join(destination, name)
  if (target !== file.source) {
    await mkdir(path.dirname(target), { recursive: true })
    await cp(file.source, target)
  }
}
const manifest = { schemaVersion: 1, packages: [...packages.values()] }
const temporary = path.join(destination, 'release.json.tmp')
await writeFile(temporary, JSON.stringify(manifest, null, 2) + '\n')
await rename(temporary, path.join(destination, 'release.json'))
console.log(`已校验并合并 ${manifest.packages.length} 个运行包：${destination}`)
