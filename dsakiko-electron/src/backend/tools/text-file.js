import { constants } from 'node:fs'
import { open } from 'node:fs/promises'
import { analyse } from 'chardet'
import { failure, requireValue } from '../core/support.js'

/**
 * @typedef {object} DecodedText
 * @property {string} text 统一为 LF 换行的文本。
 * @property {string} encoding 解码器规范化后的编码名。
 * @property {'explicit' | 'bom' | 'validated' | 'detected'} encodingSource 编码来自指定、BOM、UTF-8 校验或统计推测。
 */

function decoderFor(encoding) {
  try {
    return new TextDecoder(encoding, { fatal: true })
  } catch {
    throw failure(
      'unsupported_text_encoding',
      `不支持编码 ${encoding}，请指定当前运行时支持的文本编码。`
    )
  }
}

function decode(data, decoder, encodingSource) {
  let text
  try {
    text = decoder.decode(data)
  } catch {
    throw failure(
      'text_decoding_failed',
      `文件无法按 ${decoder.encoding} 严格解码，请检查文件内容或更换 encoding。`
    )
  }
  return { text: text.replace(/\r\n?/g, '\n'), encoding: decoder.encoding, encodingSource }
}

function bomEncoding(data) {
  // UTF-32LE 与 UTF-16LE 的前两个字节相同，必须先识别较长签名，避免误读。
  if (data.subarray(0, 4).equals(Buffer.from([0xff, 0xfe, 0, 0]))) return 'utf-32le'
  if (data.subarray(0, 4).equals(Buffer.from([0, 0, 0xfe, 0xff]))) return 'utf-32be'
  if (data.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))) return 'utf-8'
  if (data.subarray(0, 2).equals(Buffer.from([0xff, 0xfe]))) return 'utf-16le'
  if (data.subarray(0, 2).equals(Buffer.from([0xfe, 0xff]))) return 'utf-16be'
  return null
}

function encodingCandidates(data) {
  const candidates = new Map()
  for (const candidate of analyse(data)) {
    let encoding = candidate.name.toLowerCase()
    try {
      encoding = new TextDecoder(encoding).encoding
    } catch {
      // 保留运行时不支持的候选，避免把它悄悄替换成低排名的单字节编码。
    }
    const previous = candidates.get(encoding)
    if (!previous || candidate.confidence > previous.confidence)
      candidates.set(encoding, { encoding, confidence: candidate.confidence })
  }
  return [...candidates.values()].sort((left, right) => right.confidence - left.confidence)
}

function previewText(text) {
  const nonAscii = text.search(/[^\p{ASCII}]/u)
  const start = Math.max(0, nonAscii - 40)
  let preview = ''
  let count = 0
  // 先解码，再按码点截取，避免切断多字节序列或补充平面字符的代理对。
  for (const character of text.slice(start)) {
    if (count === 200) break
    preview += character
    count += 1
  }
  return preview
}

function candidatePreviews(data, candidates) {
  // 同分时优先本项目常见的中日文编码，避免短中文的正确候选被前三项限制挤掉。
  const preferred = ['gb18030', 'gbk', 'big5', 'shift_jis']
  function priority(encoding) {
    const index = preferred.indexOf(encoding)
    return index < 0 ? preferred.length : index
  }
  const shortlist = [...candidates]
    .sort(
      (left, right) =>
        right.confidence - left.confidence || priority(left.encoding) - priority(right.encoding)
    )
    .slice(0, 5)
  const groups = []
  for (const candidate of shortlist) {
    let text
    try {
      text = decode(data, decoderFor(candidate.encoding), 'detected').text
    } catch {
      // 只有整份有界文件严格解码成功才展示预览，不能只校验前缀而掩盖后面的坏字节。
      continue
    }
    const equivalent = groups.find((group) => group.text === text)
    if (equivalent) {
      equivalent.value.alternatives.push(candidate)
    } else if (groups.length < 3) {
      groups.push({ text, value: { ...candidate, preview: previewText(text), alternatives: [] } })
    }
  }
  return groups.map((group) => group.value)
}

/**
 * 使用标准解码器和 chardet 识别文本，不以扩展名或零字节判定二进制文件。
 * @param {Buffer} data 已按大小上限读取的原始字节。
 * @param {string} [encoding] auto 或调用方明确指定的编码名。
 * @returns {DecodedText} 文本及其编码来源；统计推测不保证原始编码一定正确。
 */
export function decodeText(data, encoding = 'auto') {
  requireValue(
    typeof encoding === 'string' && encoding.trim().length > 0,
    'unsupported_text_encoding'
  )
  const requested = encoding.trim().toLowerCase()
  if (requested !== 'auto') return decode(data, decoderFor(requested), 'explicit')
  const bom = bomEncoding(data)
  if (bom) return decode(data, decoderFor(bom), 'bom')

  let utf8
  try {
    utf8 = decode(data, decoderFor('utf-8'), 'validated')
  } catch {
    // 严格 UTF-8 失败后再使用统计识别，不能用替换字符吞掉损坏字节。
  }
  if (utf8 && !data.includes(0)) return utf8
  const candidates = encodingCandidates(data)
  const best = candidates[0]
  const runnerUp = candidates[1]?.confidence ?? 0
  // 分数只用于保守筛选，不是概率；短文本或候选接近时要求明确指定编码。
  const confident = best && best.confidence >= 80 && best.confidence - runnerUp >= 20
  if (confident && (!utf8 || best.encoding.startsWith('utf-16'))) {
    try {
      return decode(data, decoderFor(best.encoding), 'detected')
    } catch {
      // 推测编码还必须通过实际解码；不支持或字节非法时返回候选，让调用方明确选择。
    }
  }
  // NUL 只是 UTF-16 的识别线索，本身不是拒绝合法 UTF-8 文本的理由。
  if (utf8) return utf8
  const hints = candidates
    .slice(0, 5)
    .map((item) => `${item.encoding}（${item.confidence}）`)
    .join('、')
  const error = failure(
    'ambiguous_text_encoding',
    `无法可靠识别文本编码。候选及识别分数：${hints || '无'}。candidates 提供可严格解码的候选文本片段（优先非 ASCII 附近，最多 200 字符）；alternatives 表示相同解码结果的其他编码。分数不是正确概率，预览仅供判断且可能仍是乱码；结合文件来源选择 encoding 后重新调用。无法确定时请询问用户，不必强行选择。`
  )
  error.problem.candidates = candidatePreviews(data, candidates)
  throw error
}

/**
 * 读取已授权的真实路径，统一约束文件大小、取消和解码行为。
 * @param {string} target 经调用方完成路径授权的真实文件路径。
 * @param {{maxBytes: number, encoding?: string}} options 读取上限和编码。
 * @param {AbortSignal} [signal] 取消信号。
 * @returns {Promise<DecodedText>} 可供分页和搜索共用的文本。
 */
export async function readTextFile(target, { maxBytes, encoding }, signal) {
  signal?.throwIfAborted()
  const file = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
  try {
    const info = await file.stat()
    requireValue(info.isFile(), 'file_not_regular')
    requireValue(info.size <= maxBytes, 'file_too_large', '文件超过文本工具读取上限。')
    const bytes = Buffer.alloc(maxBytes + 1)
    let length = 0
    while (length < bytes.length) {
      signal?.throwIfAborted()
      const result = await file.read(bytes, length, bytes.length - length, length)
      if (!result.bytesRead) break
      length += result.bytesRead
    }
    requireValue(length <= maxBytes, 'file_too_large')
    signal?.throwIfAborted()
    return decodeText(bytes.subarray(0, length), encoding)
  } finally {
    await file.close()
  }
}
