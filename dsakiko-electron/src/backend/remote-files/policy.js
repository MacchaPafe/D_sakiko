import Ajv from 'ajv'
import defaults from './rules.json' with { type: 'json' }
import { copy, readJson, requireValue, failure } from '../core/support.js'

/**
 * @typedef {object} AttachmentConstraints
 * @property {boolean} [enabled] false 明确禁止该附件或传送路径。
 * @property {string[]} [mediaTypes] MIME 允许集合，缺省不限制，空集合禁止全部。
 * @property {number} [maxBytes] 单附件字节数上限。
 */
/**
 * @typedef {AttachmentConstraints & {
 *   match: {provider?: string, protocol?: string, modelIds?: string[]},
 *   transports?: {inline?: AttachmentConstraints, 'file-api'?: AttachmentConstraints}
 * }} AttachmentRule 按连接和模型匹配的限制，多个匹配项取交集。
 */
/** @typedef {{version: 1, rules: AttachmentRule[]}} AttachmentRules 每次请求固定的规则快照。 */
/** @typedef {{transport: 'inline' | 'file-api', inline: boolean, reason: string}} AttachmentDecision 路径选择与可回退条件。 */

const constraints = {
  enabled: { type: 'boolean' },
  mediaTypes: {
    type: 'array',
    uniqueItems: true,
    items: { type: 'string', pattern: '^[a-z0-9.+-]+/(?:[a-z0-9.+-]+|\\*)$' }
  },
  maxBytes: { type: 'integer', minimum: 1 }
}
const validate = new Ajv().compile({
  type: 'object',
  additionalProperties: false,
  required: ['version', 'rules'],
  properties: {
    version: { const: 1 },
    rules: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['match'],
        properties: {
          match: {
            type: 'object',
            additionalProperties: false,
            properties: {
              provider: { type: 'string', minLength: 1 },
              protocol: { type: 'string', minLength: 1 },
              modelIds: { type: 'array', items: { type: 'string', minLength: 1 } }
            }
          },
          ...constraints,
          transports: {
            type: 'object',
            additionalProperties: false,
            properties: Object.fromEntries(
              ['inline', 'file-api'].map((name) => [
                name,
                {
                  type: 'object',
                  additionalProperties: false,
                  properties: constraints
                }
              ])
            )
          }
        }
      }
    }
  }
})

/** 读取一次规则快照；文件损坏不可退化为空规则。
 * @param {string | null} file 用户规则文件，未装配持久服务时使用内置规则。
 * @returns {Promise<AttachmentRules>} 已校验的规则快照。
 */
export async function readAttachmentRules(file = null) {
  let value
  try {
    value = file ? await readJson(file) : copy(defaults)
  } catch {
    throw failure('invalid_attachment_rules', '附件规则 JSON 无法读取或解析。')
  }
  requireValue(validate(value), 'invalid_attachment_rules', '附件规则格式不合法。')
  return value
}

/** 返回首次初始化需要的独立默认值。
 * @returns {object} 初始规则。
 */
export function defaultAttachmentRules() {
  return copy(defaults)
}

/** MIME 参数不参与能力匹配。
 * @param {string} value MIME 字符串。
 * @returns {string} 规范化 MIME。
 */
export function normalizeMediaType(value) {
  const normalized = value.split(';', 1)[0].trim().toLowerCase()
  return normalized === 'image/jpg' ? 'image/jpeg' : normalized
}

function accepts(rule, info) {
  if (rule.enabled === false) return false
  if (rule.maxBytes !== undefined && info.sizeBytes > rule.maxBytes) return false
  return (
    !rule.mediaTypes ||
    rule.mediaTypes.some(
      (type) =>
        type === info.mediaType ||
        (type.endsWith('/*') && info.mediaType.startsWith(type.slice(0, -1)))
    )
  )
}

/** 同时命中的规则取交集，后面的宽松规则不能绕过已有禁止项。
 * @param {AttachmentRules} snapshot 本次请求规则快照。
 * @param {object} connection 固定连接。
 * @param {object} info 本地附件元数据。
 * @param {boolean} canUpload SDK 是否提供上传实现。
 * @returns {AttachmentDecision} 传送决策。
 */
export function selectAttachmentTransport(snapshot, connection, info, canUpload) {
  const capability = info.mediaType.startsWith('image/') ? 'imageInput' : 'fileInput'
  requireValue(
    connection.capabilities?.[capability]?.status !== 'unsupported',
    'unsupported_input',
    '当前模型明确不支持此附件输入。'
  )
  const rules = snapshot.rules.filter(
    ({ match }) =>
      (!match.provider || match.provider === connection.provider) &&
      (!match.protocol || match.protocol === connection.protocol) &&
      (!match.modelIds || match.modelIds.includes(connection.modelId))
  )
  requireValue(
    rules.every((rule) => accepts(rule, info)),
    'unsupported_input',
    '附件类型或大小不符合附件规则。'
  )
  const inline = rules.every((rule) => accepts(rule.transports?.inline ?? {}, info))
  const upload =
    canUpload &&
    connection.capabilities?.fileUpload?.status !== 'unsupported' &&
    rules.every((rule) => accepts(rule.transports?.['file-api'] ?? {}, info))
  const selected = connection.attachmentTransport ?? 'auto'
  requireValue(['auto', 'inline', 'file-api'].includes(selected), 'invalid_connection')
  if (selected === 'inline' || (selected === 'auto' && !upload)) {
    requireValue(inline, 'unsupported_input', '没有可用的附件传送方式。')
    return {
      transport: 'inline',
      inline,
      reason: selected === 'inline' ? 'configured_inline' : 'upload_unavailable'
    }
  }
  requireValue(upload, 'file_upload_unavailable', '当前连接没有可用的文件上传能力。')
  return {
    transport: 'file-api',
    inline,
    reason: selected === 'auto' ? 'prefer_upload' : 'configured_upload'
  }
}
