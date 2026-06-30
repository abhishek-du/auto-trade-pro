import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../api/client'

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
      const data = await apiFetch(`/api/v1/ipo/?${params}`)
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
      const data = await apiFetch('/api/v1/ipo/stats/summary')
      setStats(data)
      setDataSource(data.source || null)
    } catch { }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      await apiFetch('/api/v1/ipo/refresh', { method: 'POST' })
      await fetchIpos()
      await fetchStats()
    } finally {
      setLoading(false)
    }
  }, [fetchIpos, fetchStats])

  const fetchAnalysis = useCallback(async (slug, forceRefresh = false) => {
    return apiFetch(`/api/v1/ipo/${slug}/analysis${forceRefresh ? '?refresh=true' : ''}`)
  }, [])

  useEffect(() => {
    fetchIpos()
    fetchStats()
  }, [fetchIpos, fetchStats])

  return { ipos, loading, error, stats, cachedAt, dataSource, fetchIpos, refresh, fetchAnalysis }
}
