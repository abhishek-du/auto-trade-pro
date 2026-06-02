import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../api/client'

export function useBreadth() {
  const [breadth,  setBreadth]  = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [history,  setHistory]  = useState([])

  const loadHistory = useCallback(() => {
    apiFetch('/api/v1/india/breadth/history')
      .then(setHistory)
      .catch(() => {})
  }, [])

  useEffect(() => {
    apiFetch('/api/v1/india/breadth')
      .then(data => { setBreadth(data); setLoading(false) })
      .catch(() => setLoading(false))

    loadHistory()

    const id = setInterval(() => {
      apiFetch('/api/v1/india/breadth')
        .then(setBreadth)
        .catch(() => {})
      loadHistory()
    }, 120_000)

    return () => clearInterval(id)
  }, [loadHistory])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      await apiFetch('/api/v1/india/breadth/refresh', { method: 'POST' })
      const full = await apiFetch('/api/v1/india/breadth')
      setBreadth(full)
      loadHistory()
    } finally {
      setLoading(false)
    }
  }, [loadHistory])

  return { breadth, loading, history, refresh }
}
