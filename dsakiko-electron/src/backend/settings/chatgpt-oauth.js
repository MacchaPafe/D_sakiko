import http from 'node:http'
import { createHash, randomBytes } from 'node:crypto'
import { once } from 'node:events'
import { createLocalJWKSet, jwtVerify } from 'jose'
import { failure, requireValue } from '../core/support.js'

export const issuer = 'https://auth.openai.com'
export const resource = 'https://api.openai.com/v1'
export const planScope = 'chatgpt.tokens.use.direct'

/** 只允许认证服务自己的 HTTPS 地址取得认证材料。 */
function authUrl(url) {
  requireValue(new URL(url).origin === issuer, 'authentication', '认证服务端点无效。')
  return url
}

/** 令牌、授权码和服务端响应均不进入对外错误。 */
async function authRequest(url, options = {}, transport = fetch) {
  const response = await transport(url, {
    ...options,
    redirect: 'error',
    signal: options.signal ?? AbortSignal.timeout(30000)
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const code = data.error?.code ?? data.error
    const invalid = [
      'invalid_grant',
      'invalid_refresh_token',
      'refresh_token_reused',
      'invalid_client'
    ].includes(code)
    throw failure(
      invalid ? 'login_expired' : 'authentication',
      invalid
        ? 'ChatGPT 登录已失效，请重新登录。'
        : 'ChatGPT 授权服务请求失败，请检查账号权限和网络。'
    )
  }
  return response
}

/** @param {object} params 授权码或刷新请求。 @param {typeof fetch} transport 网络传输。 @returns {Promise<object>} 令牌端点响应。 */
export async function tokenRequest(params, transport) {
  const response = await authRequest(
    issuer + '/api/accounts/oauth/token',
    {
      method: 'POST',
      body: new URLSearchParams({ ...params, resource })
    },
    transport
  )
  return response.json()
}

async function discovery(transport) {
  const data = await (
    await authRequest(issuer + '/.well-known/openid-configuration', {}, transport)
  ).json()
  requireValue(data.issuer === issuer, 'authentication')
  return data
}

/** @param {string} refreshToken 当前刷新令牌。 @param {string} clientId 注册标识。 @param {typeof fetch} transport 网络传输。 @returns {Promise<void>} 完成远端撤销。 */
export async function revoke(refreshToken, clientId, transport) {
  const info = await discovery(transport)
  await authRequest(
    authUrl(info.revocation_endpoint),
    {
      method: 'POST',
      body: new URLSearchParams({
        token: refreshToken,
        token_type_hint: 'refresh_token',
        client_id: clientId
      })
    },
    transport
  )
}

/** @param {string} token 身份令牌。 @param {string} clientId 预期受众。 @param {string|null} nonce 当前登录挑战。 @param {typeof fetch} transport 网络传输。 @returns {Promise<object>} 已验证的稳定身份和邮箱。 */
export async function verifyIdentity(token, clientId, nonce, transport) {
  try {
    const info = await discovery(transport)
    const keys = await (await authRequest(authUrl(info.jwks_uri), {}, transport)).json()
    const { payload } = await jwtVerify(token, createLocalJWKSet(keys), {
      algorithms: ['RS256', 'ES256'],
      issuer,
      audience: clientId,
      requiredClaims: ['exp', 'sub']
    })
    requireValue(
      typeof payload.sub === 'string' && payload.sub && (!nonce || payload.nonce === nonce)
    )
    return { subject: payload.sub, email: typeof payload.email === 'string' ? payload.email : '' }
  } catch {
    throw failure('authentication', 'ChatGPT 返回的身份未通过验证，请重新登录。')
  }
}

/** 使用系统浏览器和临时回环端口完成一次授权，应用关闭或取消时释放监听。
 * @param {object} profile 选中的注册。 @param {Function} openBrowser 打开系统浏览器。 @param {AbortSignal} signal 取消信号。 @returns {Promise<object>} 调用令牌端点所需的交换参数。
 */
export async function authorize(profile, openBrowser, signal) {
  const random = () => randomBytes(32).toString('base64url')
  const state = random(),
    nonce = random(),
    verifier = random()
  const { promise, resolve, reject } = Promise.withResolvers()
  // 浏览器启动失败时，回调等待也必须能够正常取消。
  promise.catch(() => {})
  const cancel = () => reject(failure('cancelled', 'ChatGPT 登录已取消或超时。'))
  const server = http.createServer((request, response) => {
    const url = URL.parse(request.url, 'http://127.0.0.1')
    if (!url) {
      response.writeHead(400).end()
      return
    }
    const valid =
      request.method === 'GET' &&
      url.pathname === '/auth/callback' &&
      url.searchParams.getAll('state').length === 1 &&
      url.searchParams.get('state') === state
    response.writeHead(valid ? 200 : 400, {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
      Connection: 'close'
    })
    response.end(valid ? '授权结果已收到。请返回数字小祥。' : '无效的登录回调。')
    if (valid) resolve(url.searchParams)
  })
  try {
    signal.throwIfAborted()
    signal.addEventListener('abort', cancel, { once: true })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const redirect = 'http://127.0.0.1:' + server.address().port + '/auth/callback'
    const params = new URLSearchParams({
      client_id: profile.clientId || 'dynamic_agent_client',
      ext_agent_host_id: profile.hostId,
      response_type: 'code',
      redirect_uri: redirect,
      scope: 'openid profile email offline_access resource.invoke ' + planScope,
      resource,
      state,
      nonce,
      code_challenge: createHash('sha256').update(verifier).digest('base64url'),
      code_challenge_method: 'S256'
    })
    if (!profile.clientId) params.set('agent_name_hint', '数字小祥')
    if (profile.clientId && profile.email) params.set('login_hint', profile.email)
    if (profile.tokens?.id_token) params.set('id_token_hint', profile.tokens.id_token)
    if (profile.needsConsent) params.set('prompt', 'consent')
    await openBrowser(issuer + '/api/accounts/authorize?' + params)
    const result = await promise
    const clientId = result.get('client_id') || profile.clientId
    requireValue(
      !result.has('error') &&
        result.getAll('code').length === 1 &&
        result.getAll('client_id').length <= 1 &&
        result.get('code') &&
        clientId &&
        clientId !== 'dynamic_agent_client' &&
        (!profile.clientId || profile.clientId === clientId),
      'authentication',
      '未完成授权，或返回的注册与所选账号不同。'
    )
    return {
      clientId,
      nonce,
      exchange: {
        grant_type: 'authorization_code',
        client_id: clientId,
        code: result.get('code'),
        code_verifier: verifier,
        redirect_uri: redirect
      }
    }
  } finally {
    signal.removeEventListener('abort', cancel)
    server.close()
    server.closeAllConnections()
  }
}
