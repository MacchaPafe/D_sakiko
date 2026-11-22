import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'

export const catalogTextSaveDelay = 5_000
const catalogSaveMaxWait = 30_000

/** @typedef {{startedAt: number, saveAt: number}} CatalogSaveSchedule 本批修改的开始时间和自动保存期限，均以单调时钟计时。 */

/**
 * @param {CatalogSaveSchedule|null} previous 当前批次的计时。
 * @param {number} delay 本次编辑需要等待的停顿时长，单位为毫秒。
 * @param {number} now 本次编辑的单调时钟时间，单位为毫秒。
 * @returns {CatalogSaveSchedule} 最新的自动保存期限。
 */
export function nextCatalogSaveSchedule(previous, delay, now) {
  const startedAt = previous?.startedAt ?? now
  const deadline = Math.min(now + delay, startedAt + catalogSaveMaxWait)
  // 一旦已到期，新输入就不能推迟该次保存，包括由离散操作触发的即时保存。
  const saveAt = previous && previous.saveAt <= now ? previous.saveAt : deadline
  return { startedAt, saveAt }
}

/**
 * @template {{revision: number}} T
 * @param {{initial?: T|null, paused: boolean, persist: (base: T|null, edited: T) => Promise<T>, accept: (saved: T, submitted: T) => T}} options 保存回调负责校验和持久化，accept 保留用户仍在输入的格式。
 * @returns {{base: T|null, edited: T|null, dirty: boolean, isNew: boolean, saving: boolean, error?: string, select: (value: T|null, whenClean?: boolean) => boolean, change: (update: (value: T|null) => T, delay?: number) => void, commit: () => Promise<boolean>, flush: () => Promise<boolean>, waitForSave: () => Promise<boolean>|undefined}} 草稿、保存状态与创建、切换入口。
 */
export function useCatalogEditor({ initial = null, paused, persist, accept }) {
  const [draft, setDraft] = useState(() => ({
    base: initial?.revision ? initial : null,
    edited: initial,
    clean: initial?.revision ? initial : null,
    schedule: null
  }))
  const current = useRef(draft)
  const flight = useRef(null)
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState(null)
  const { base, edited } = draft
  const dirty = edited !== draft.clean
  const isNew = !base?.revision

  const publish = useCallback((value) => {
    current.current = value
    setDraft(value)
  }, [])

  const select = useCallback(
    (value, whenClean = false) => {
      if (whenClean && current.current.edited !== current.current.clean) return false
      setFailure(null)
      publish({ base: value, edited: value, clean: value, schedule: null })
      return true
    },
    [publish]
  )

  function change(update, delay = 0) {
    const previous = current.current
    const job = flight.current
    const startsBatch = job?.submitted === previous || failure?.draft === previous
    const schedule = nextCatalogSaveSchedule(
      startsBatch ? null : previous.schedule,
      delay,
      performance.now()
    )
    job?.updates.push(update)
    setFailure(null)
    publish({ ...previous, edited: update(previous.edited), schedule })
  }

  function commit() {
    if (flight.current) return flight.current.promise
    const submitted = current.current
    if (submitted.edited === submitted.clean) return Promise.resolve(true)
    const job = { submitted, updates: [], promise: null }
    flight.current = job
    setFailure(null)
    setSaving(true)
    job.promise = Promise.resolve()
      .then(() => persist(submitted.base, submitted.edited))
      .then((saved) => {
        const clean = accept(saved, submitted.edited)
        // 保存期间继续接收输入，把后来的编辑应用到后端返回的新版本。
        publish({
          base: saved,
          clean,
          edited: job.updates.reduce((value, update) => update(value), clean),
          schedule: job.updates.length ? current.current.schedule : null
        })
        return true
      })
      .catch((error) => {
        if (current.current === submitted) setFailure({ draft: submitted, message: error.message })
        return false
      })
      .finally(() => {
        flight.current = null
        setSaving(false)
      })
    return job.promise
  }

  function waitForSave() {
    return flight.current?.promise
  }

  async function flush() {
    // 导航时跳过等待输入停顿的计时，也提交正在写盘期间收到的最后一笔编辑。
    while (current.current.base?.revision && current.current.edited !== current.current.clean) {
      const submitted = flight.current?.submitted ?? current.current
      if (!(await commit()) && current.current === submitted) return false
    }
    return true
  }

  const saveAfterDelay = useEffectEvent((scheduledDraft) => {
    if (!paused && current.current === scheduledDraft) void commit()
  })

  useEffect(() => {
    if (paused || isNew || !dirty || saving || failure?.draft === draft || !draft.schedule) return
    const remaining = Math.max(0, Math.ceil(draft.schedule.saveAt - performance.now()))
    const timer = setTimeout(() => saveAfterDelay(draft), remaining)
    return () => clearTimeout(timer)
  }, [paused, isNew, dirty, saving, failure, draft])

  return {
    base,
    edited,
    dirty,
    isNew,
    saving,
    error: failure?.message,
    select,
    change,
    commit,
    flush,
    waitForSave
  }
}
