import { useState, useEffect, useCallback } from 'react'

export function useBreadth() {
  const [breadth,  setBreadth]  = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [history,  setHistory]  = useState([])

  const loadHistory = useCallback(() => {
    fetch('/api/v1/india/breadth/history')
      .then(r => r.json())
      .then(setHistory)
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/v1/india/breadth')
      .then(r => r.json())
      .then(data => { setBreadth(data); setLoading(false) })
      .catch(() => setLoading(false))

    loadHistory()

    const id = setInterval(() => {
      fetch('/api/v1/india/breadth')
        .then(r => r.json())
        .then(setBreadth)
        .catch(() => {})
      loadHistory()
    }, 120_000)

    return () => clearInterval(id)
  }, [loadHistory])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r    = await fetch('/api/v1/india/breadth/refresh', { method: 'POST' })
      const data = await r.json()
      // After refresh, fetch the full breadth structure
      const full = await fetch('/api/v1/india/breadth').then(r2 => r2.json())
      setBreadth(full)
      loadHistory()
    } finally {
      setLoading(false)
    }
  }, [loadHistory])

  return { breadth, loading, history, refresh }
}
