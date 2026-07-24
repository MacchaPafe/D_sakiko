import { createContext, useContext } from 'react'

export const RuntimeContext = createContext(null)

export function useRuntime() {
  const context = useContext(RuntimeContext)
  if (!context) throw new Error('useRuntime must be used inside RuntimeProvider')
  return context
}
