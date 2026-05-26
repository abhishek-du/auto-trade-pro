import { useState, useEffect, useCallback } from 'react'

export function useIPOTracker() {
  const [ipos,    setIpos]    = useState([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [stats,      setStats]      = useState(null)
  const [cachedAt,   setCachedAt]   = useState(null)
  const [dataSource, setDataSource] = useState(null)   // 'ipoalerts' | 'nse_fallback'

  const fetchIpos = useCallback(async (status = null, ipoType = null) => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (status)  params.set('status',  status)
      if (ipoType) params.set('type',    ipoType)
      params.set('limit', '100')
      const res  = await fetch(`/api/v1/ipo/?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setIpos(data.ipos || [])
      setCachedAt(data.cached_at)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchStats = useCallback(async () => {
    try {
      const res  = await fetch('/api/v1/ipo/stats/summary')
      const data = await res.json()
      setStats(data)
      setDataSource(data.source || null)
    } catch { }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      await fetch('/api/v1/ipo/refresh', { method: 'POST' })
      await fetchIpos()
      await fetchStats()
    } finally {
      setLoading(false)
    }
  }, [fetchIpos, fetchStats])

  const fetchAnalysis = useCallback(async (slug, forceRefresh = false) => {
    const url = `/api/v1/ipo/${slug}/analysis${forceRefresh ? '?refresh=true' : ''}`
    const res  = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }, [])

  useEffect(() => {
    fetchIpos()
    fetchStats()
  }, [fetchIpos, fetchStats])

  return { ipos, loading, error, stats, cachedAt, dataSource, fetchIpos, refresh, fetchAnalysis }
}
