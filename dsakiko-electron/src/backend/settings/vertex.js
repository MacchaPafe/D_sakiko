import { createPrivateKey } from 'node:crypto'
import { requireValue, uuid } from '../core/support.js'

/** @param {object} value 用户提交的字段。 @returns {{project:string,location:string,email:string}} 官方 Vertex 服务所需的项目、区域和身份。 */
export function vertexFields(value) {
  const { project, location, email } = value ?? {}
  requireValue(
    typeof project === 'string' && /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(project),
    'invalid_vertex_project',
    '请填写有效的 Google Cloud 项目 ID。'
  )
  requireValue(
    typeof location === 'string' && /^[a-z][a-z0-9-]{0,61}[a-z0-9]$/.test(location),
    'invalid_vertex_location',
    '请填写有效的 Vertex 区域。'
  )
  requireValue(
    typeof email === 'string' && /^[^\s@]+@[^\s@]+\.gserviceaccount\.com$/.test(email),
    'invalid_vertex_email',
    '请填写服务账号邮箱。'
  )
  return { project, location, email }
}

/** @param {string} value PEM 私钥。 @returns {string} 可用于服务账号签名的 RSA 私钥。 */
export function vertexPrivateKey(value) {
  try {
    if (
      typeof value === 'string' &&
      value.length < 32768 &&
      createPrivateKey(value).asymmetricKeyType === 'rsa'
    )
      return value
  } catch {
    /* 解析错误可能包含本机信息，转为统一提示。 */
  }
  requireValue(false, 'invalid_vertex_key', '服务账号私钥无效，请重新选择 JSON 或替换私钥。')
}

/** 临时私钥只留在后端；保存后原始下载文件可以删除。 */
export class VertexDrafts {
  entries = new Map()
  stage(data) {
    requireValue(
      data?.type === 'service_account',
      'invalid_vertex_key',
      '请选择 Google 服务账号 JSON 文件。'
    )
    const vertex = vertexFields({
      project: data.project_id,
      location: 'global',
      email: data.client_email
    })
    const key = vertexPrivateKey(data.private_key)
    const ticket = uuid()
    const timer = setTimeout(() => this.discard(ticket), 30 * 60 * 1000)
    timer.unref()
    this.entries.set(ticket, { key, timer })
    return { vertex, ticket }
  }
  get(ticket) {
    requireValue(
      this.entries.has(ticket),
      'vertex_draft_expired',
      '凭据草稿已过期，请重新选择 JSON。'
    )
    return this.entries.get(ticket).key
  }
  discard(ticket) {
    clearTimeout(this.entries.get(ticket)?.timer)
    this.entries.delete(ticket)
  }
}
