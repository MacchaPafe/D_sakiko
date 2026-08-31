const headerName = 'X-Dsakiko-Pairing-UI'
const storageKey = 'dsakiko-pairing-ui-nonce'
const fragment = new URLSearchParams(window.location.hash.slice(1))
const fragmentNonce = fragment.get('ui')
if (fragmentNonce) window.sessionStorage.setItem(storageKey, fragmentNonce)
const nonce = fragmentNonce || window.sessionStorage.getItem(storageKey) || ''
window.history.replaceState(null, '', window.location.pathname)

const unauthorized = document.querySelector('#unauthorized')
const content = document.querySelector('#content')
const qr = document.querySelector('#qr')
const qrWrap = document.querySelector('.qr-wrap')
const regenerate = document.querySelector('#regenerate')
const overlayTitle = document.querySelector('#overlay-title')
const status = document.querySelector('#status')
const statusBadge = document.querySelector('.status-badge')
const address = document.querySelector('#address')
const refresh = document.querySelector('#refresh')
const pairingLink = document.querySelector('#pairing-link')
const copy = document.querySelector('#copy')
const fallbackUrl = document.querySelector('#fallback-url')
const accessCode = document.querySelector('#access-code')
const accessCodeCells = document.querySelector('#access-code-cells')
const copyCode = document.querySelector('#copy-code')
const message = document.querySelector('#message')

let lastRevision = -1
let latestState = null
let hasRevealedPairing = false
let messageTimer = null

function showMessage(text, isSuccess = false) {
  if (messageTimer) window.clearTimeout(messageTimer)
  message.textContent = text
  message.classList.toggle('is-success', isSuccess)
  message.style.opacity = '1'
  messageTimer = window.setTimeout(() => {
    message.style.opacity = '0'
    messageTimer = window.setTimeout(() => {
      message.textContent = ''
      message.classList.remove('is-success')
    }, 200)
  }, 3200)
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: 'no-store',
    headers: {
      [headerName]: nonce,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) throw new Error(response.status === 403 ? 'unauthorized' : 'request_failed')
  return response.json()
}

function formatRemaining(seconds) {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return minutes > 0 ? `${minutes} 分 ${remainder} 秒后过期` : `${remainder} 秒后过期`
}

function renderAccessCode(code) {
  const codeStr = (code || '------').trim()
  if (accessCode) accessCode.textContent = codeStr
  if (accessCodeCells) {
    const cells = accessCodeCells.querySelectorAll('.digit-cell')
    for (let i = 0; i < 6; i += 1) {
      if (cells[i]) {
        cells[i].textContent = codeStr[i] || '-'
      }
    }
  }
}

function renderAddresses(state) {
  const signature = state.addresses.map((item) => `${item.address}:${item.interface_name}`).join('|')
  if (address.dataset.signature === signature && address.value === (state.selected_address || '')) return
  address.dataset.signature = signature
  address.replaceChildren()
  for (const item of state.addresses) {
    const option = document.createElement('option')
    option.value = item.address
    option.textContent = `${item.address}`
    option.selected = item.address === state.selected_address
    address.append(option)
  }
  address.disabled = state.addresses.length === 0
}

async function refreshPresentation(force = false) {
  if (!force && latestState && latestState.revision === lastRevision) return
  const data = await request('/api/presentation')
  lastRevision = data.revision
  renderAccessCode(data.access_code)
  pairingLink.value = data.pairing_url || ''
  fallbackUrl.textContent = data.fallback_url || '未检测到可用地址'
  fallbackUrl.href = data.fallback_url || '#'
  qr.innerHTML = data.qr_svg || '<span class="no-network">未检测到可用局域网地址</span>'
  if (data.qr_svg && !hasRevealedPairing) {
    hasRevealedPairing = true
    qrWrap.classList.add('is-revealing')
    window.setTimeout(() => qrWrap.classList.remove('is-revealing'), 400)
  }
}

function renderState(state) {
  latestState = state
  renderAddresses(state)
  regenerate.classList.toggle('hidden', state.status === 'active')
  overlayTitle.textContent = state.status === 'used' ? '设备已连接' : '二维码已过期'

  if (statusBadge) {
    statusBadge.classList.toggle('is-expired', state.status === 'expired')
    statusBadge.classList.toggle('is-used', state.status === 'used')
  }

  if (!state.selected_address) {
    status.textContent = '未检测到可用私有 IPv4，请检查网络。'
  } else if (state.status === 'active') {
    status.textContent = formatRemaining(state.remaining_seconds)
  } else if (state.status === 'used') {
    status.textContent = '设备已连接，可以关闭此页面了。'
  } else {
    status.textContent = '二维码已过期，需重新生成。'
  }
}

async function load() {
  try {
    const state = await request('/api/state')
    unauthorized.classList.add('hidden')
    content.classList.remove('hidden')
    renderState(state)
    await refreshPresentation()
  } catch (error) {
    if (error.message === 'unauthorized') {
      content.classList.add('hidden')
      unauthorized.classList.remove('hidden')
      return
    }
    showMessage('暂时无法读取配对状态，请稍后重试。')
  }
}

async function copyAccessCodeToClipboard() {
  const code = accessCode?.textContent?.trim()
  if (!code || code === '------') return
  try {
    await navigator.clipboard.writeText(code)
    showMessage('6 位访问码已复制到剪贴板。', true)
  } catch {
    showMessage('复制失败，请手动记录访问码。')
  }
}

regenerate.addEventListener('click', async () => {
  if (latestState?.connected && !window.confirm('新设备成功连接后会中断当前设备的连接，继续吗？')) return
  await request('/api/regenerate', { method: 'POST' })
  lastRevision = -1
  await load()
})

refresh.addEventListener('click', async () => {
  await request('/api/refresh', { method: 'POST' })
  lastRevision = -1
  await load()
})

address.addEventListener('change', async () => {
  await request('/api/address', {
    method: 'POST',
    body: JSON.stringify({ address: address.value }),
  })
  lastRevision = -1
  await load()
})

copy.addEventListener('click', async () => {
  if (!pairingLink.value) return
  try {
    await navigator.clipboard.writeText(pairingLink.value)
    showMessage('自动配对链接已复制。', true)
  } catch {
    showMessage('复制失败，请手动选择复制。')
  }
})

if (copyCode) {
  copyCode.addEventListener('click', copyAccessCodeToClipboard)
}

if (accessCodeCells) {
  accessCodeCells.addEventListener('click', copyAccessCodeToClipboard)
}

if (!nonce) {
  unauthorized.classList.remove('hidden')
} else {
  load()
  window.setInterval(load, 1000)
}

