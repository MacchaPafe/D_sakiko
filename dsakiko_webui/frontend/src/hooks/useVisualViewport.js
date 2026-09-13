import { useEffect } from 'react'

export function useVisualViewport() {
  useEffect(() => {
    const viewport = window.visualViewport
    document.documentElement.style.setProperty('--stage-height', `${window.innerHeight}px`)

    const updateViewport = () => {
      const height = viewport?.height || window.innerHeight
      const offsetTop = viewport?.offsetTop || 0
      document.documentElement.style.setProperty('--app-height', `${height}px`)
      document.documentElement.style.setProperty('--app-offset-top', `${offsetTop}px`)
    }

    updateViewport()
    viewport?.addEventListener('resize', updateViewport)
    viewport?.addEventListener('scroll', updateViewport)
    window.addEventListener('resize', updateViewport)
    const updateStageAfterOrientation = () => {
      window.setTimeout(() => {
        document.documentElement.style.setProperty('--stage-height', `${window.innerHeight}px`)
        updateViewport()
      }, 180)
    }
    window.addEventListener('orientationchange', updateStageAfterOrientation)

    return () => {
      viewport?.removeEventListener('resize', updateViewport)
      viewport?.removeEventListener('scroll', updateViewport)
      window.removeEventListener('resize', updateViewport)
      window.removeEventListener('orientationchange', updateStageAfterOrientation)
    }
  }, [])
}
