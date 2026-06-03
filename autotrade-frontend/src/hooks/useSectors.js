import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../api/client'

export function useSectors() {
  const [sectors,         setSectors]         = useState([])
  const [selectedSector,  setSelectedSector]  = useState(null)
  const [rotation,        setRotation]        = useState(null)
  const [loading,         setLoading]         = useState(true)

  const load = useCallback(() => {
    Promise.all([
      apiFetch('/api/v1/india/sectors/summary'),
      apiFetch('/api/v1/india/sectors/rotation'),
    ]).then(([summary, rot]) => {
      setSectors(Array.isArray(summary) ? summary : [])
      setRotation(rot)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [load])

  const selectSector = useCallback(async (sectorKey) => {
    if (!sectorKey) { setSelectedSector(null); return }
    try {
      // apiFetch returns parsed JSON directly.
      const data = await apiFetch(`/api/v1/india/sectors/${sectorKey}`)
      setSelectedSector(data)
    } catch { /* ignore */ }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/api/v1/india/sectors/refresh', { method: 'POST' })
      setSectors(Array.isArray(data) ? data : [])
      const rot = await apiFetch('/api/v1/india/sectors/rotation')
      setRotation(rot)
    } finally {
      setLoading(false)
    }
  }, [])

  const sorted         = [...sectors].sort((a, b) => b.avg_change_pct - a.avg_change_pct)
  const bestSector     = sorted[0]  || null
  const worstSector    = sorted[sorted.length - 1] || null

  return {
    sectors, loading, rotation,
    selectedSector, selectSector,
    refresh, bestSector, worstSector,
  }
}
