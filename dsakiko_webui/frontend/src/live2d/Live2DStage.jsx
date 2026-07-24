import { useEffect, useRef, useState } from 'react'
import { Application, Ticker, UPDATE_PRIORITY } from 'pixi.js'
import {
  Live2DModel,
  MotionPriority,
} from 'pixi-live2d-display/cubism2'

Live2DModel.registerTicker(Ticker)

export function Live2DStage({
  modelUrl,
  active,
  motionGroup,
  mouthOpenRef,
}) {
  const hostRef = useRef(null)
  const appRef = useRef(null)
  const modelRef = useRef(null)
  const activeRef = useRef(active)
  const [status, setStatus] = useState('loading')
  const [error, setError] = useState('')

  useEffect(() => {
    const host = hostRef.current
    if (!host || !modelUrl) return undefined

    let app
    let model
    let resizeObserver
    let disposed = false

    const fitModel = () => {
      if (!app || !model || disposed) return
      const width = app.screen.width
      const height = app.screen.height
      if (width <= 0 || height <= 0) return

      model.scale.set(1)
      const scale = Math.min(
        (width * 0.92) / model.width,
        (height * 0.92) / model.height,
      )
      model.scale.set(scale)
      model.position.set(width / 2, height * 0.48)
    }

    const resizeCanvas = () => {
      if (!app || disposed) return
      app.renderer.resize(
        Math.max(host.clientWidth, 1),
        Math.max(host.clientHeight, 1),
      )
      fitModel()
    }

    const load = async () => {
      setStatus('loading')
      setError('')
      try {
        if (!window.Live2D) {
          throw new Error('Cubism 2 Core 未加载')
        }

        app = new Application({
          antialias: true,
          autoDensity: true,
          backgroundAlpha: 0,
          resolution: Math.min(window.devicePixelRatio || 1, 2),
        })
        app.view.className = 'live2d-canvas'
        host.replaceChildren(app.view)
        appRef.current = app

        resizeObserver = new ResizeObserver(resizeCanvas)
        resizeObserver.observe(host)
        resizeCanvas()

        model = await Live2DModel.from(modelUrl, {
          autoInteract: false,
          idleMotionGroup: 'idle_motion',
        })
        if (disposed) {
          model.destroy({ children: true })
          return
        }

        model.anchor.set(0.5, 0.5)
        modelRef.current = model
        app.stage.addChild(model)
        fitModel()

        app.ticker.add(() => {
          const coreModel = modelRef.current?.internalModel?.coreModel
          if (!coreModel?.setParamFloat) return
          coreModel.setParamFloat(
            'PARAM_MOUTH_OPEN_Y',
            Math.min(1, mouthOpenRef.current * 1.35),
          )
        }, undefined, UPDATE_PRIORITY.LOW)

        setStatus('ready')
        if (!activeRef.current) app.ticker.stop()
      } catch (loadError) {
        if (disposed) return
        console.error('Live2D model loading failed:', loadError)
        setStatus('error')
        setError(loadError instanceof Error ? loadError.message : String(loadError))
      }
    }

    load()

    return () => {
      disposed = true
      resizeObserver?.disconnect()
      modelRef.current = null
      appRef.current = null
      if (app) {
        app.destroy(true, {
          children: true,
          texture: true,
          baseTexture: true,
        })
      }
    }
  }, [modelUrl, mouthOpenRef])

  useEffect(() => {
    activeRef.current = active
    const ticker = appRef.current?.ticker
    if (!ticker) return
    if (active) ticker.start()
    else ticker.stop()
  }, [active])

  useEffect(() => {
    const model = modelRef.current
    if (!active || status !== 'ready' || !model || !motionGroup) return
    if (motionGroup === 'idle_motion') return
    model.motion(motionGroup, undefined, MotionPriority.FORCE).catch(() => {})
  }, [active, motionGroup, status])

  return (
    <div className="live2d-stage" aria-label="Live2D 角色">
      <div ref={hostRef} className="live2d-stage__canvas" />
      {status === 'loading' && (
        <div className="stage-status" role="status">
          <span className="loading-spinner" aria-hidden="true" />
          <span>角色载入中</span>
        </div>
      )}
      {status === 'error' && (
        <div className="stage-status stage-status--error" role="alert">
          <strong>角色加载失败</strong>
          <span>{error}</span>
        </div>
      )}
    </div>
  )
}
