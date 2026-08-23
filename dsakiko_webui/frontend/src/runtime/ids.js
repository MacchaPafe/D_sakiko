export function randomId(prefix) {
  const value = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}_${Math.random().toString(16).slice(2)}`
  return `${prefix}_${value}`
}
