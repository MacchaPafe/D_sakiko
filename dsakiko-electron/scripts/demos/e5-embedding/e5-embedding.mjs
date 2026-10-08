import { AutoModel, AutoTokenizer, env, mean_pooling } from '@huggingface/transformers'

/**
 * @typedef {Object} E5Encoder
 * @property {(texts: string[]) => Promise<number[][]>} encode 编码已包含 E5 前缀的文本。
 * @property {(texts: string[]) => Promise<number[][]>} encodeQueries 编码查询文本。
 * @property {(texts: string[]) => Promise<number[][]>} encodePassages 编码知识正文。
 * @property {() => Promise<void>} close 释放模型会话。
 */

/**
 * 生成与 Python 单段输入一致的 E5 词元，保留截断后的结束符。
 * @param {import('@huggingface/transformers').PreTrainedTokenizer} tokenizer 本地分词器。
 * @param {string[]} texts 已添加前缀的文本。
 * @returns {Object} 可传给模型的输入张量。
 */
export function tokenizeE5(tokenizer, texts) {
  const inputs = tokenizer(texts, { padding: true, truncation: true, max_length: 512 })
  const width = inputs.input_ids.dims[1]
  if (width === 512) {
    for (let row = 0; row < texts.length; row += 1) {
      const last = row * width + width - 1
      // Transformers.js 3.8.1 在添加特殊词元后直接裁剪；Python 会为结束符预留位置。
      if (inputs.attention_mask.data[last] === 1n) {
        inputs.input_ids.data[last] = BigInt(tokenizer.eos_token_id)
      }
    }
  }
  return inputs
}

/**
 * 加载本地 FP32 E5 ONNX 模型；运行期间不访问网络。
 * @param {string} modelDirectory 包含配置、tokenizer 和 onnx/model.onnx 的绝对路径。
 * @param {{threads?: number, batchSize?: number}} options CPU 线程数及编码批量。
 * @returns {Promise<E5Encoder>} 可复用的查询和正文编码器。
 */
export async function createE5Encoder(modelDirectory, { threads = 4, batchSize = 32 } = {}) {
  if (!Number.isInteger(threads) || threads < 1 || !Number.isInteger(batchSize) || batchSize < 1) {
    throw new RangeError('threads 和 batchSize 必须为正整数')
  }
  env.allowRemoteModels = false
  env.useFSCache = false
  const tokenizer = await AutoTokenizer.from_pretrained(modelDirectory)
  const model = await AutoModel.from_pretrained(modelDirectory, {
    dtype: 'fp32',
    device: 'cpu',
    session_options: { intraOpNumThreads: threads, interOpNumThreads: 1 }
  })

  /** @param {string[]} texts 已包含前缀的文本。 @returns {Promise<number[][]>} 归一化的 384 维向量。 */
  async function encode(texts) {
    if (!Array.isArray(texts) || texts.some((text) => typeof text !== 'string' || !text.trim())) {
      throw new TypeError('texts 必须为非空字符串数组')
    }
    const vectors = []
    for (let offset = 0; offset < texts.length; offset += batchSize) {
      const inputs = tokenizeE5(tokenizer, texts.slice(offset, offset + batchSize))
      const outputs = await model(inputs)
      const pooled = mean_pooling(outputs.last_hidden_state, inputs.attention_mask)
      vectors.push(...pooled.normalize(2, -1).tolist())
    }
    return vectors
  }

  /** @param {string[]} texts 原始文本。 @param {string} prefix E5 前缀。 @returns {Promise<number[][]>} 句向量。 */
  async function encodeWithPrefix(texts, prefix) {
    if (!Array.isArray(texts) || texts.some((text) => typeof text !== 'string' || !text.trim())) {
      throw new TypeError('texts 必须为非空字符串数组')
    }
    return encode(texts.map((text) => `${prefix}: ${text.trim()}`))
  }

  /** @param {string[]} texts 查询文本。 @returns {Promise<number[][]>} 查询向量。 */
  async function encodeQueries(texts) {
    return encodeWithPrefix(texts, 'query')
  }

  /** @param {string[]} texts 知识正文。 @returns {Promise<number[][]>} 正文向量。 */
  async function encodePassages(texts) {
    return encodeWithPrefix(texts, 'passage')
  }

  /** @returns {Promise<void>} 会话释放完成。 */
  async function close() {
    await model.dispose()
  }

  return { encode, encodeQueries, encodePassages, close }
}

/**
 * 计算已经 L2 归一化的两个向量的余弦相似度。
 * @param {number[]} left 第一个向量。
 * @param {number[]} right 第二个向量。
 * @returns {number} 相似度，值越大越相关。
 */
export function similarity(left, right) {
  if (left.length !== right.length || left.length === 0) {
    throw new RangeError('向量维度必须相同且非空')
  }
  let score = 0
  for (let index = 0; index < left.length; index += 1) {
    score += left[index] * right[index]
  }
  return score
}
