import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { copyFile, mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { resourcePath } from '../../src/backend/resources/files.js'

const specs = [
  { id: 'sakiko-white', source: 'adv_live2d_sakiko_010_casual_spring_01' },
  { id: 'sakiko-black', source: 'adv_live2d_sakiko_010_live_01' },
  { id: 'anon-default', source: 'adv_live2d_anon_002_casual_spring_01' },
  { id: 'soyo-default', source: 'adv_live2d_soyo_004_casual_spring_01' }
]

/**
 * 打包用户指定来源中的四套完整 V3 模型及现有角色资料；不改写原始模型。
 * @param {{modelsRoot: string, charactersRoot: string, output: string}} options 打包输入。
 * @returns {Promise<import('../../src/backend/onboarding/interface.js').BuiltinManifest>} Onboarding 可直接读取的清单。
 */
export async function packBuiltinCharacters({ modelsRoot, charactersRoot, output }) {
  try {
    await stat(output)
    throw new Error('输出目录已存在，请另选目录，核对后再替换旧包。')
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  const staging = output + '.staging-' + process.pid
  await mkdir(staging, { recursive: false })
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
    const manifest = { version: 1, characters }
    await writeFile(path.join(staging, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
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
      output: { type: 'string', default: 'resources/builtin-characters' }
    }
  })
  if (!values['models-root'] || !values['characters-root'])
    throw new Error('请提供 --models-root 与 --characters-root。')
  const manifest = await packBuiltinCharacters({
    modelsRoot: path.resolve(values['models-root']),
    charactersRoot: path.resolve(values['characters-root']),
    output: path.resolve(values.output)
  })
  console.log(
    JSON.stringify({
      version: manifest.version,
      characters: manifest.characters.length,
      models: manifest.characters.reduce((sum, role) => sum + role.forms.length, 0)
    })
  )
}
