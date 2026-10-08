import { parseArgs } from 'node:util'
import { fileURLToPath } from 'node:url'
import * as lancedb from '@lancedb/lancedb'
import { createE5Encoder } from './e5-embedding.mjs'
import { importWorldbook, loadOfficialWorldbook, searchWorldbook } from './lancedb-worldbook.mjs'

const root = new URL('../../../../', import.meta.url)
const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    type: { type: 'string', default: 'lore_entry' },
    time: { type: 'string', default: '4099' },
    character: { type: 'string', default: 'anon' },
    target: { type: 'string' }
  }
})
const command = positionals[0]
if (!['build', 'query'].includes(command)) throw new Error('用法：npm run lancedb -- build | query "问题" [--type 类型 --time 时间 --character 角色 --target 对象]')
const databasePath = process.env.E5_LANCEDB_PATH ?? fileURLToPath(new URL('.scratch/e5-lancedb-demo/', root))
const modelPath = process.env.E5_MODEL_PATH ?? fileURLToPath(new URL('GPT_SoVITS/pretrained_models/multilingual-e5-small-onnx/', root))
const packagePath = process.env.E5_WORLDBOOK_PATH ?? fileURLToPath(new URL('GPT_SoVITS/rag/worldbooks/official/official.bang_dream.its_mygo/', root))
const db = await lancedb.connect(databasePath)
let encoder
let table
try {
  encoder = await createE5Encoder(modelPath)
  if (command === 'build') {
    const rows = await loadOfficialWorldbook(packagePath)
    table = await importWorldbook(db, encoder, rows)
    console.log(JSON.stringify({ databasePath, count: await table.countRows() }, null, 2))
  } else {
    const query = positionals[1] ?? 'CRYCHIC 是什么乐队？'
    table = await db.openTable('worldbook')
    const [vector] = await encoder.encodeQueries([query])
    const filter = {
      packageIds: ['official.bang_dream.its_mygo'], timelineId: 'bang_dream_original',
      canonBranch: 'main', entryType: values.type, queryTime: Number(values.time)
    }
    if (values.type === 'story_event') filter.knownByCharacterId = values.character
    if (['character_thought', 'character_relation'].includes(values.type)) filter.subjectCharacterId = values.character
    if (values.target !== undefined) filter.objectCharacterId = values.target
    const results = await searchWorldbook(table, vector, filter, { boostSource: query })
    console.log(JSON.stringify({ query, filter, results }, null, 2))
  }
} finally {
  table?.close()
  db.close()
  await encoder?.close()
}
