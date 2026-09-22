const PAIRING_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/

export function readAndClearPairingToken(browserWindow = window) {
  const fragment = new URLSearchParams(browserWindow.location.hash.slice(1))
  const candidate = fragment.get('pair')
  if (browserWindow.location.hash) {
    browserWindow.history.replaceState(
      null,
      '',
      `${browserWindow.location.pathname}${browserWindow.location.search}`,
    )
  }
  return candidate && PAIRING_TOKEN_PATTERN.test(candidate) ? candidate : null
}

export function nextAuthenticationStep(authenticated, pairingToken) {
  if (authenticated) return 'connect'
  return pairingToken ? 'redeem_pairing' : 'request_access_code'
}
