const CLIENT_ID_STORAGE_KEY = 'dsakiko-webui-client-id'

function clientId() {
  let value = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY)
  if (!value) {
    value = randomId('web')
    window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, value)
  }
  return value
}

export async function getHealth() {
  const response = await fetch('/api/v1/health', {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error('无法连接到电脑端服务。')
  return response.json()
}

export async function createSession(accessCode) {
  const response = await fetch('/api/v1/session', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      access_code: accessCode,
      session_id: clientId(),
    }),
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(body.error?.message || '登录失败，请稍后重试。')
    error.code = body.error?.code || 'AUTH_REQUIRED'
    throw error
  }
  return body
}
import { randomId } from './ids'
