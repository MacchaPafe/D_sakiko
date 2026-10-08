import { fileURLToPath } from 'node:url'
import { createE5Encoder, similarity } from './e5-embedding.mjs'

const defaultModel = fileURLToPath(
  new URL('../../../../GPT_SoVITS/pretrained_models/multilingual-e5-small-onnx/', import.meta.url)
)
const encoder = await createE5Encoder(process.env.E5_MODEL_PATH ?? defaultModel)
try {
  const query = process.argv[2] ?? '祥子为什么离开了乐队？'
  const passages = [
    '祥子离开了原来的乐队，此后组建了新的乐队。',
    '今天的天气晴朗，适合出门散步。',
    '乐队成员一起练习并准备下一场演出。'
  ]
  const [queryVector] = await encoder.encodeQueries([query])
  const passageVectors = await encoder.encodePassages(passages)
  const ranked = passages.map((text, index) => ({
    text,
    score: similarity(queryVector, passageVectors[index])
  }))
  ranked.sort((left, right) => right.score - left.score)
  console.log(JSON.stringify({ query, dimensions: queryVector.length, results: ranked }, null, 2))
} finally {
  await encoder.close()
}
