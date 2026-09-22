import { useCallback, useEffect, useRef, useState } from 'react'
import { Application, Ticker, UPDATE_PRIORITY } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display'
import { Live2DRuntimeController } from './Live2DRuntimeController'

Live2DModel.registerTicker(Ticker)

export function Live2DStage({
  presentation,
  presentationReason,
  active,
  cue,
  mouthOpenRef,
  onRetryPresentation,
  onRuntimeStateChange,
}) {
  const hostRef = useRef(null)
  const controllerRef = useRef(null)
  const initialActiveRef = useRef(active)
  const mouthSyncFrameRef = useRef(0)
  const mouthOpenValueRef = useRef(0)
  const runtimeStateChangeRef = useRef(onRuntimeStateChange)
  const [runtimeState, setRuntimeState] = useState({
    status: presentation?.resolution === 'absent' ? 'absent' : 'loading',
    error: '',
    retryable: false,
  })

  useEffect(() => {
    runtimeStateChangeRef.current = onRuntimeStateChange
  }, [onRuntimeStateChange])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    const app = new Application({
      antialias: true,
      autoDensity: true,
      backgroundAlpha: 0,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
    })
    app.view.className = 'live2d-canvas'
    host.replaceChildren(app.view)

    let controller
    const fit = () => {
      app.renderer.resize(
        Math.max(host.clientWidth, 1),
        Math.max(host.clientHeight, 1),
      )
      controller?.fit(app.screen.width, app.screen.height)
    }
    controller = new Live2DRuntimeController({
      app,
      onStatusChange: (nextState) => {
        setRuntimeState(nextState)
        runtimeStateChangeRef.current?.(nextState)
      },
      onModelChange: fit,
    })
    controllerRef.current = controller
    const resizeObserver = new ResizeObserver(fit)
    resizeObserver.observe(host)
    fit()
    app.ticker.add(() => {
      if (mouthSyncFrameRef.current % 3 === 0) {
        mouthOpenValueRef.current = Math.min(1, mouthOpenRef.current * 1.4)
      }
      controller.updateFrame(mouthOpenValueRef.current)
      mouthSyncFrameRef.current += 1
    }, undefined, UPDATE_PRIORITY.LOW)
    controller.setActive(initialActiveRef.current)

    return () => {
      resizeObserver.disconnect()
      controller.destroy()
      controllerRef.current = null
      app.destroy(true, {
        children: true,
        texture: true,
        baseTexture: true,
      })
    }
  }, [mouthOpenRef])

  useEffect(() => {
    controllerRef.current?.setActive(active)
  }, [active])

  useEffect(() => {
    controllerRef.current?.setPresentation(presentation, {
      reason: presentationReason || 'snapshot',
    })
  }, [presentation, presentationReason])

  useEffect(() => {
    controllerRef.current?.setCue(cue)
  }, [cue])

  const retry = useCallback(() => {
    if (presentation?.resolution === 'configured_error') {
      onRetryPresentation?.()
      return
    }
    controllerRef.current?.retry()
  }, [onRetryPresentation, presentation?.resolution])

  return (
    <div className="live2d-stage" aria-label="Live2D 角色">
      <div ref={hostRef} className="live2d-stage__canvas" />
      {runtimeState.status === 'loading' && (
        <div className="stage-status" role="status">
          <span className="loading-spinner" aria-hidden="true" />
          <span>{runtimeState.error || '角色载入中'}</span>
        </div>
      )}
      {runtimeState.status === 'error' && (
        <div className="stage-status stage-status--error" role="alert">
          <strong>角色加载失败</strong>
          <span>{runtimeState.error}</span>
          {runtimeState.retryable && (
            <button type="button" className="stage-retry-button" onClick={retry}>
              重试
            </button>
          )}
        </div>
      )}
    </div>
  )
}
