import { randomId } from './ids'

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

export async function uploadImage(file) {
  const formData = new FormData()
  formData.append('file', file, file.name)
  const response = await fetch('/api/v1/uploads/images', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(body.error?.message || '图片上传失败，请重试。')
    error.code = body.error?.code || 'IMAGE_UPLOAD_FAILED'
    throw error
  }
  return body
}

export async function deleteUploadedImage(uploadId) {
  await fetch(`/api/v1/uploads/images/${encodeURIComponent(uploadId)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
}

async function settingsRequest(method, body) {
  const response = await fetch('/api/v1/settings', {
    method,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(result.error?.message || '设置读取失败，请稍后重试。')
    error.code = result.error?.code || 'SETTINGS_FAILED'
    throw error
  }
  return result
}

export function getSettings() {
  return settingsRequest('GET')
}

export function updateSettings(settings) {
  return settingsRequest('PATCH', settings)
}
