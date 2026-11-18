import { useCallback, useEffect, useState } from 'react'

/** @typedef {import('../../../../backend/live2d-import/outfits.js').OutfitJob} OutfitJob */
/** @typedef {import('../../../../shared/contracts/setup.js').SetupSnapshot} ResourceJob */
/** @typedef {{items: OutfitJob[]|ResourceJob[], error: string|null}} DownloadQuery */

const emptyQuery = { items: [], error: null }

/**
 * @param {import('../../backend/client.js').BackendClient} client 应用连接。
 * @param {boolean} open 下载中心或服装管理是否打开。
 * @returns {{outfits: DownloadQuery, resources: DownloadQuery, loading: boolean, refresh: () => void}} 独立保留两类任务的上次结果。
 */
export function useDownloadJobs(client, open) {
  const [queries, setQueries] = useState(null)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision((value) => value + 1), [])
  useEffect(() => {
    let active = true
    let timer
    async function poll() {
      const [outfits, resources] = await Promise.allSettled([
        client.outfits.listJobs(),
        client.setup.listJobs()
      ])
      if (!active) return
      setQueries((previous) => ({
        outfits: result(outfits, previous?.outfits),
        resources: result(resources, previous?.resources)
      }))
      // 等本轮结束才开始计时，连接慢时也只有一个查询在途。
      timer = setTimeout(poll, open ? 1000 : 4000)
    }
    void poll()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [client, open, revision])
  return {
    outfits: queries?.outfits ?? emptyQuery,
    resources: queries?.resources ?? emptyQuery,
    loading: !queries,
    refresh
  }
}

function result(response, previous) {
  if (response.status === 'fulfilled') return { items: response.value, error: null }
  return { items: previous?.items ?? [], error: response.reason.message }
}

/**
 * @param {Array<OutfitJob|ResourceJob>} jobs 任务快照。
 * @returns {Array<OutfitJob|ResourceJob>} 下载中心需要显示的执行中和可恢复任务。
 */
export function pendingDownloads(jobs) {
  return jobs.filter((job) => {
    if ('kind' in job && job.kind !== 'download') return false
    return !['planned', 'succeeded', 'cancelled'].includes(job.status)
  })
}
