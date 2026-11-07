import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { randomUUID } from 'node:crypto'
import { copyFile, mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import * as tar from 'tar'
import { resourcePath } from '../../src/backend/resources/files.js'
import { walk, sourcePath } from '../../src/backend/setup/files.js'
import { requireValue, safeId } from '../../src/backend/core/support.js'

const specs = [
  { id: 'sakiko-white', source: 'adv_live2d_sakiko_010_casual_spring_01' },
  { id: 'sakiko-black', source: 'adv_live2d_sakiko_010_live_01' },
  { id: 'anon-default', source: 'adv_live2d_anon_002_casual_spring_01' },
  { id: 'soyo-default', source: 'adv_live2d_soyo_004_casual_spring_01' }
]

/** 拒绝覆盖已存在的包；中间目录与目标位于同一卷。 */
async function createStaging(output) {
  try {
    await stat(output)
    throw new Error('输出目录已存在，请另选目录，核对后再替换旧包。')
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  const staging = output + '.staging-' + randomUUID()
  await mkdir(staging, { recursive: false })
  return staging
}

/** 每个角色一份归档；仅记录内容版本与大小，不生成逐文件校验清单。 */
async function writeArchives(source, output, manifest) {
  requireValue(manifest.version === 1 && manifest.characters.length > 0, 'invalid_builtin_manifest')
  const characters = []
  const ids = new Set()
  for (const role of manifest.characters) {
    safeId(role.id)
    requireValue(!ids.has(role.id) && !role.archive, 'invalid_builtin_manifest')
    ids.add(role.id)
    const files = []
    let expandedBytes = 0
    // 把欢迎形态排在包首；正式默认形态和清单顺序不变。
    const forms = [...role.forms].sort(
      (left, right) =>
        Number(right.id === role.previewFormId) - Number(left.id === role.previewFormId)
    )
    for (const form of forms) {
      const directory = await sourcePath(source, form.directory)
      requireValue(
        (await stat(await sourcePath(directory, form.entry))).isFile(),
        'builtin_resources_missing'
      )
      for (const name of await walk(directory)) {
        const file = form.directory + '/' + name
        files.push(file)
        expandedBytes += (await stat(resourcePath(source, file))).size
      }
    }
    requireValue(
      files.length > 0 && new Set(files).size === files.length,
      'invalid_builtin_manifest'
    )
    const archive = 'archives/' + role.id + '.tar.gz'
    await mkdir(path.join(output, 'archives'), { recursive: true })
    await tar.c(
      {
        file: resourcePath(output, archive),
        cwd: source,
        gzip: { level: 6 },
        portable: true,
        noMtime: true
      },
      files
    )
    characters.push({ ...role, archive, expandedBytes })
  }
  const result = { ...manifest, contentVersion: randomUUID(), characters }
  await writeFile(path.join(output, 'manifest.json'), JSON.stringify(result, null, 2) + '\n')
  return result
}

/**
 * 将已有目录版内置包转换为三个角色归档，保留完整人设和形态元数据。
 * @param {{source: string, output: string}} options 原始内置包与不存在的新目录。
 * @returns {Promise<import('../../src/backend/onboarding/interface.js').BuiltinManifest>} 压缩包清单。
 */
export async function compressBuiltinCharacters({ source, output }) {
  const staging = await createStaging(output)
  try {
    const manifest = JSON.parse(await readFile(path.join(source, 'manifest.json'), 'utf8'))
    const result = await writeArchives(source, staging, manifest)
    await rename(staging, output)
    return result
  } finally {
    await rm(staging, { recursive: true, force: true })
  }
}

/**
 * 打包用户指定来源中的四套完整 V3 模型及现有角色资料；不改写原始模型。
 * @param {{modelsRoot: string, charactersRoot: string, output: string}} options 打包输入。
 * @returns {Promise<import('../../src/backend/onboarding/interface.js').BuiltinManifest>} Onboarding 可直接读取的清单。
 */
export async function packBuiltinCharacters({ modelsRoot, charactersRoot, output }) {
  const staging = await createStaging(output)
  try {
    const models = new Map()
    for (const spec of specs) {
      const source = path.join(modelsRoot, spec.source)
      const entry = spec.source + '.model3.json'
      const model = JSON.parse(await readFile(path.join(source, entry), 'utf8'))
      const refs = model.FileReferences
      const filenames = new Set(
        [
          entry,
          refs.Moc,
          ...refs.Textures,
          refs.Physics,
          refs.Pose,
          refs.UserData,
          refs.DisplayInfo,
          ...(refs.Expressions ?? []).map((item) => item.File),
          ...Object.values(refs.Motions ?? {}).flatMap((group) =>
            group.flatMap((item) => [item.File, item.Sound])
          )
        ].filter((item) => item !== undefined)
      )
      const directory = 'models/' + spec.id
      const destination = path.join(staging, directory)
      for (const filename of [...filenames].sort()) {
        const from = resourcePath(source, filename)
        const to = resourcePath(destination, filename)
        await mkdir(path.dirname(to), { recursive: true })
        await copyFile(from, to)
      }
      models.set(spec.id, { directory, entry })
    }
    const characters = []
    for (const id of ['sakiko', 'anon', 'soyo']) {
      const source = path.join(charactersRoot, id)
      const name = (await readFile(path.join(source, 'name.txt'), 'utf8')).trim()
      const description = (
        await readFile(path.join(source, 'character_description.txt'), 'utf8')
      ).trim()
      const formIds = id === 'sakiko' ? ['black', 'white'] : ['default']
      const forms = formIds.map((formId) => {
        const modelId = id + '-' + formId
        let formDescription = description
        if (id === 'sakiko')
          formDescription +=
            '\n\n当前形态：' +
            (formId === 'white'
              ? '白祥。请使用上文的白祥性格与语气。'
              : '黑祥。请使用上文的黑祥性格与语气。')
        return {
          id: formId,
          displayName: name,
          description: formDescription,
          modelName: formId === 'black' ? '演出服' : '春季常服',
          ...models.get(modelId)
        }
      })
      characters.push({
        id,
        name,
        description,
        themeColor: null,
        defaultFormId: id === 'sakiko' ? 'black' : 'default',
        previewFormId: id === 'sakiko' ? 'white' : 'default',
        forms
      })
    }
    const manifest = await writeArchives(staging, staging, { version: 1, characters })
    await rm(path.join(staging, 'models'), { recursive: true })
    await rename(staging, output)
    return manifest
  } catch (error) {
    await rm(staging, { recursive: true, force: true })
    throw error
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({
    options: {
      'models-root': { type: 'string' },
      'characters-root': { type: 'string' },
      source: { type: 'string' },
      output: { type: 'string', default: 'resources/builtin-characters' }
    }
  })
  let manifest
  if (values.source) {
    if (values['models-root'] || values['characters-root'])
      throw new Error('--source 不能与原始模型来源选项混用。')
    manifest = await compressBuiltinCharacters({
      source: path.resolve(values.source),
      output: path.resolve(values.output)
    })
  } else {
    if (!values['models-root'] || !values['characters-root'])
      throw new Error('请提供 --source，或同时提供 --models-root 与 --characters-root。')
    manifest = await packBuiltinCharacters({
      modelsRoot: path.resolve(values['models-root']),
      charactersRoot: path.resolve(values['characters-root']),
      output: path.resolve(values.output)
    })
  }
  console.log(
    JSON.stringify({
      version: manifest.version,
      characters: manifest.characters.length,
      models: manifest.characters.reduce((sum, role) => sum + role.forms.length, 0)
    })
  )
}
