import { useEffect, useState } from 'react'

/** @param {object[]} lotteries 当前会话的抽签记录。 @returns {number} 到达最近的揭晓时刻才更新，历史视图不参与动画逐帧重绘。 */
export function useRevealClock(lotteries) {
  const [now, setNow] = useState(Date.now)
  const next = Math.min(...(lotteries ?? []).map((item) => item.revealAt).filter((at) => at > now))
  useEffect(() => {
    if (!Number.isFinite(next)) return
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, next - Date.now()))
    return () => clearTimeout(timer)
  }, [next])
  return now
}
