import { useState, useEffect, useCallback } from 'react'

const BASE = '/api/v1/intelligence'

export function useIntelligenceHub() {
  const [context,     setContext]     = useState(null)
  const [scores,      setScores]      = useState([])
  const [mfSignals,   setMFSignals]   = useState([])
  const [cycleLog,    setCycleLog]    = useState([])
  const [loading,     setLoading]     = useState(true)
  const [lastCycleAt, setLastCycleAt] = useState(null)
  const [triggering,  setTriggering]  = useState(false)

  const loadAll = useCallback(async () => {
    const [ctx, sc, mf, log] = await Promise.allSettled([
      fetch(`${BASE}/context`).then(r => r.ok ? r.json() : null),
      fetch(`${BASE}/scores?limit=50`).then(r => r.ok ? r.json() : []),
      fetch(`${BASE}/mf-signals`).then(r => r.ok ? r.json() : []),
      fetch(`${BASE}/cycle-log?limit=10`).then(r => r.ok ? r.json() : []),
    ])
    if (ctx.status === 'fulfilled' && ctx.value) setContext(ctx.value)
    if (sc.status  === 'fulfilled') setScores(Array.isArray(sc.value) ? sc.value : [])
    if (mf.status  === 'fulfilled') setMFSignals(Array.isArray(mf.value) ? mf.value : [])
    if (log.status === 'fulfilled') {
      const arr = Array.isArray(log.value) ? log.value : []
      setCycleLog(arr)
      setLastCycleAt(arr[0]?.cycle_end || arr[0]?.cycle_start || null)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    loadAll()
    const id = setInterval(loadAll, 60_000)
    return () => clearInterval(id)
  }, [loadAll])

  async function triggerCycle() {
    setTriggering(true)
    try {
      await fetch(`${BASE}/trigger`, { method: 'POST' })
      // Cycle takes ~20-30s; poll a few times
      setTimeout(loadAll, 8_000)
      setTimeout(loadAll, 25_000)
    } catch {}
    finally {
      setTimeout(() => setTriggering(false), 25_000)
    }
  }

  return { context, scores, mfSignals, cycleLog, loading, lastCycleAt, triggering, triggerCycle, reload: loadAll }
}
