import { useCallback, useEffect, useState } from 'react'
import { isPendingDownload } from '../../../../shared/contracts/download-jobs.js'

/** @typedef {import('../../../../shared/contracts/download-jobs.js').DownloadJob} DownloadJob */
/** @typedef {import('../../../../shared/contracts/download-jobs.js').DownloadUpdate} DownloadUpdate */

const emptyItems = []

/** @param {DownloadJob[]} items 当前任务集合。 @param {DownloadUpdate} event 服务端快照或增量。 @returns {DownloadJob[]} 更新后的集合。 */
function applyUpdate(items, event) {
  if (event.kind === 'snapshot') return event.items
  if (event.kind === 'remove') return items.filter((job) => job.id !== event.id)
  const changes = new Map(event.items.map((job) => [job.id, job]))
  const updated = items.map((job) => {
    const change = changes.get(job.id)
    changes.delete(job.id)
    return change ?? job
  })
  return [...changes.values(), ...updated]
}

function useJobDetails(client, topic, open, revision) {
  const [items, setItems] = useState(null)
  useEffect(() => {
    if (!open) return
    return client.observe(topic, 'details', (event) => {
      setItems((previous) => {
        const updated = applyUpdate(previous ?? emptyItems, event)
        if (topic === 'setup-jobs') return [...updated].sort((a, b) => b.expiresAt - a.expiresAt)
        return updated
      })
    })
  }, [client, topic, open, revision])
  return items
}

/**
 * @param {import('../../backend/client.js').BackendClient} client 应用连接。
 * @param {{outfits: boolean, resources: boolean, backgrounds: boolean}} open 当前界面需要显示的任务详情。
 * @returns {{outfits: DownloadJob[], resources: DownloadJob[], backgrounds: DownloadJob[], count: number, loading: boolean, error: string|null, refresh: () => void}} 首页待处理数量与面板任务详情。
 */
export function useDownloadJobs(client, open) {
  const [pendingOutfits, setPendingOutfits] = useState(0)
  const [pendingBackgrounds, setPendingBackgrounds] = useState(0)
  const [pendingResources, setPendingResources] = useState(0)
  const [error, setError] = useState(null)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision((value) => value + 1), [])
  const outfits = useJobDetails(client, 'outfit-jobs', open.outfits, revision)
  const backgrounds = useJobDetails(client, 'background-jobs', open.backgrounds, revision)
  const resources = useJobDetails(client, 'setup-jobs', open.resources, revision)
  useEffect(() => {
    const stopOutfits = client.observe('outfit-jobs', 'summary', ({ count }) =>
      setPendingOutfits(count)
    )
    const stopBackgrounds = client.observe('background-jobs', 'summary', ({ count }) =>
      setPendingBackgrounds(count)
    )
    const stopResources = client.observe('setup-jobs', 'summary', ({ count }) =>
      setPendingResources(count)
    )
    const stopConnection = client.observeConnection(({ connected, problem }) => {
      setError(connected ? null : (problem?.message ?? null))
    })
    return () => {
      stopOutfits()
      stopResources()
      stopBackgrounds()
      stopConnection()
    }
  }, [client])
  return {
    outfits: outfits ?? emptyItems,
    resources: resources ?? emptyItems,
    backgrounds: backgrounds ?? emptyItems,
    count: pendingOutfits + pendingResources + pendingBackgrounds,
    loading:
      !error &&
      ((open.outfits && !outfits) ||
        (open.resources && !resources) ||
        (open.backgrounds && !backgrounds)),
    error,
    refresh
  }
}

/**
 * @template {DownloadJob} T
 * @param {T[]} jobs 任务快照。
 * @returns {T[]} 下载中心需要显示的执行中和可恢复任务。
 */
export function pendingDownloads(jobs) {
  return jobs.filter(isPendingDownload)
}
