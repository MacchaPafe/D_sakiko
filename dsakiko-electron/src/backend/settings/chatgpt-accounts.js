import path from 'node:path'
import os from 'node:os'
import { readJson, writeJson, SerialQueue, requireValue, uuid } from '../core/support.js'
import {
  authorize,
  verifyIdentity,
  revoke,
  tokenRequest,
  planScope,
  resource,
  issuer
} from './chatgpt-oauth.js'

function legacyRegistry() {
  let root = process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share')
  if (process.platform === 'darwin')
    root = path.join(os.homedir(), 'Library', 'Application Support')
  if (process.platform === 'win32')
    root = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
  return path.join(root, 'D_sakiko', 'chatgpt', 'accounts.json')
}

/** @typedef {{id:string, hostId:string, clientId?:string, email?:string, subject?:string, models?:object[], tokens?:object, needsConsent?:boolean}} ChatGPTRegistration 一个安装下的账号注册，令牌仅留在后端。 */
function summary(profile) {
  return {
    id: profile.id,
    fromLegacy: profile.fromLegacy === true,
    label: (profile.email || 'ChatGPT 账号') + ' · ' + profile.id.slice(0, 6),
    signedIn: Boolean(profile.tokens),
    connected: Boolean(profile.tokens?.refresh_token && !profile.needsConsent),
    models: profile.models ?? []
  }
}

function saveTokens(profile, data, previous = {}) {
  const refresh = data.refresh_token || previous.refresh_token
  const scopes = typeof data.scope === 'string' ? data.scope.split(/\s+/) : (previous.scopes ?? [])
  const expires = Number(data.expires_in ?? 3600)
  requireValue(
    data.access_token && Number.isFinite(expires) && expires > 0,
    'authentication',
    '登录服务未返回有效凭据。'
  )
  profile.needsConsent = !scopes.includes(planScope)
  requireValue(profile.needsConsent || refresh, 'authentication', '登录服务未返回可续期凭据。')
  profile.tokens = {
    access_token: data.access_token,
    refresh_token: refresh,
    id_token: data.id_token || previous.id_token,
    scopes,
    expiresAt: Date.now() + expires * 1000
  }
}

/** 独立管理 Electron 的注册和轮换令牌。迁移仅读 Python 注册表，不访问旧钥匙串。 */
export class ChatGPTAccounts {
  constructor(directory, { transport = fetch, legacyFile = legacyRegistry() } = {}) {
    this.file = path.join(directory, 'chatgpt-accounts.json')
    this.legacyFile = legacyFile
    this.transport = transport
    this.queue = new SerialQueue()
    this.pending = null
  }
  async state() {
    return (await readJson(this.file)) ?? { hostId: 'urn:uuid:' + uuid(), profiles: [] }
  }
  async list() {
    return (await this.state()).profiles.map(summary)
  }

  async discover() {
    return this.queue.run('accounts', async () => {
      const legacy = await readJson(this.legacyFile)
      const state = await this.state()
      const matches = new Set()
      if (
        legacy?.version === 1 &&
        /^urn:uuid:[a-f0-9-]{36}$/i.test(legacy.host_id) &&
        Array.isArray(legacy.accounts)
      ) {
        if (!state.profiles.length) state.hostId = legacy.host_id
        for (const item of legacy.accounts) {
          if (
            typeof item.client_id !== 'string' ||
            !item.client_id ||
            item.issuer !== issuer ||
            !item.subject
          )
            continue
          let profile = state.profiles.find(
            (p) => p.hostId === legacy.host_id && p.clientId === item.client_id
          )
          if (!profile) {
            profile = {
              id: uuid(),
              hostId: legacy.host_id,
              clientId: item.client_id,
              subject: item.subject,
              email: item.email || ''
            }
            state.profiles.push(profile)
          }
          profile.fromLegacy = true
          matches.add(profile)
        }
        await writeJson(this.file, state)
      }
      return Array.from(matches, summary)
    })
  }

  cancel() {
    this.pending?.abort()
  }

  /** 关闭时等待已经提交的刷新令牌完成原子写入。 */
  async close() {
    this.cancel()
    await this.queue.run('accounts', () => {})
  }

  async login(id, openBrowser) {
    requireValue(!this.pending, 'login_busy', '已有 ChatGPT 登录正在进行。')
    const controller = new AbortController()
    this.pending = controller
    try {
      const initial = await this.queue.run('accounts', async () => {
        const state = await this.state()
        if (id) {
          const profile = state.profiles.find((p) => p.id === id)
          requireValue(profile, 'account_missing', '找不到所选注册。')
          return profile
        }
        await writeJson(this.file, state)
        return { id: uuid(), hostId: state.hostId }
      })
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(180000)])
      const auth = await authorize(initial, openBrowser, signal)
      return await this.queue.run('accounts', async () => {
        const state = await this.state()
        let profile = state.profiles.find(
          (p) => p.hostId === initial.hostId && p.clientId === auth.clientId
        )
        if (!profile) {
          profile = { ...initial, clientId: auth.clientId }
          state.profiles.push(profile)
          await writeJson(this.file, state)
        }
        const data = await tokenRequest(auth.exchange, this.transport)
        const identity = await verifyIdentity(
          data.id_token,
          auth.clientId,
          auth.nonce,
          this.transport
        )
        requireValue(
          !profile.subject || profile.subject === identity.subject,
          'authentication',
          '请选择该注册原有的 ChatGPT 账号。'
        )
        signal.throwIfAborted()
        Object.assign(profile, identity)
        saveTokens(profile, data)
        await writeJson(this.file, state)
        return summary(profile)
      })
    } finally {
      this.pending = null
    }
  }

  /** 刷新在单一写队列中完成；新的刷新令牌必须持久化后才能交给请求。 */
  async access(id, rejected = null) {
    return this.queue.run('accounts', async () => {
      const state = await this.state()
      const p = state.profiles.find((item) => item.id === id)
      requireValue(
        p && summary(p).connected,
        'login_required',
        '请先在设置中登录 ChatGPT 并授权套餐用量。'
      )
      if (p.tokens.expiresAt < Date.now() + 60000 || p.tokens.access_token === rejected) {
        try {
          const data = await tokenRequest(
            {
              grant_type: 'refresh_token',
              client_id: p.clientId,
              refresh_token: p.tokens.refresh_token
            },
            this.transport
          )
          if (data.id_token) {
            const identity = await verifyIdentity(data.id_token, p.clientId, null, this.transport)
            requireValue(
              identity.subject === p.subject,
              'authentication',
              '刷新后的账号身份不一致。'
            )
          }
          saveTokens(p, data, p.tokens)
        } catch (error) {
          if (error.problem?.code === 'login_expired') {
            delete p.tokens
            await writeJson(this.file, state)
          }
          throw error
        }
        await writeJson(this.file, state)
        requireValue(!p.needsConsent, 'login_required', '请重新授权使用 ChatGPT 套餐。')
      }
      return p.tokens.access_token
    })
  }

  async models(id) {
    let token = await this.access(id)
    const request = () =>
      this.transport(resource + '/models', {
        headers: { Authorization: 'Bearer ' + token },
        redirect: 'error',
        signal: AbortSignal.timeout(30000)
      })
    let response = await request()
    if (response.status === 401) {
      const body = await response
        .clone()
        .json()
        .catch(() => null)
      if (['invalid_token', 'expired_token', 'token_expired'].includes(body?.error?.code)) {
        token = await this.access(id, token)
        response = await request()
      }
    }
    requireValue(response.ok, 'invalid_catalog', '读取订阅模型列表失败，请检查登录状态后重试。')
    const data = await response.json()
    requireValue(Array.isArray(data.models), 'invalid_catalog', 'ChatGPT 模型目录无效。')
    const models = data.models
      .filter(
        (m) => typeof m.slug === 'string' && m.visibility === 'list' && m.supported_in_api !== false
      )
      .map((m) => ({
        id: m.slug,
        label: m.display_name || m.slug,
        efforts: (m.supported_reasoning_levels ?? []).map((level) => level.effort).filter(Boolean),
        contextTokens: m.context_window,
        image: m.input_modalities?.includes('image')
      }))
    return this.queue.run('accounts', async () => {
      const state = await this.state()
      const p = state.profiles.find((item) => item.id === id)
      requireValue(p && summary(p).connected, 'login_required')
      p.models = models
      await writeJson(this.file, state)
      return models
    })
  }

  async logout(id) {
    return this.queue.run('accounts', async () => {
      const state = await this.state()
      const p = state.profiles.find((item) => item.id === id)
      requireValue(p, 'account_missing')
      let warning = ''
      if (p.tokens?.refresh_token) {
        try {
          await revoke(p.tokens.refresh_token, p.clientId, this.transport)
        } catch {
          warning = '已退出本地登录，尚未确认远端撤销。'
        }
      }
      delete p.tokens
      await writeJson(this.file, state)
      return { warning }
    })
  }
}
