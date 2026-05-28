import { useState, useEffect, useCallback } from 'react'

const BASE = '/api/v1/agent'

export function useAgent() {
  const [status,      setStatus]      = useState(null)
  const [decisions,   setDecisions]   = useState([])
  const [trades,      setTrades]      = useState([])
  const [positions,   setPositions]   = useState([])
  const [performance, setPerformance] = useState(null)
  const [loading,     setLoading]     = useState(false)
  const [cycling,     setCycling]     = useState(false)
  const [error,       setError]       = useState(null)

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/status`)
      if (r.ok) setStatus(await r.json())
    } catch {}
  }, [])

  const fetchDecisions = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/decisions?limit=15`)
      if (r.ok) setDecisions(await r.json())
    } catch {}
  }, [])

  const fetchTrades = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/trades?limit=20`)
      if (r.ok) setTrades(await r.json())
    } catch {}
  }, [])

  const fetchPositions = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/positions`)
      if (r.ok) setPositions(await r.json())
    } catch {}
  }, [])

  const fetchPerformance = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/performance`)
      if (r.ok) setPerformance(await r.json())
    } catch {}
  }, [])

  const refreshAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([
      fetchStatus(), fetchDecisions(), fetchTrades(),
      fetchPositions(), fetchPerformance(),
    ])
    setLoading(false)
  }, [fetchStatus, fetchDecisions, fetchTrades, fetchPositions, fetchPerformance])

  useEffect(() => {
    refreshAll()
    const id = setInterval(refreshAll, 30_000)
    return () => clearInterval(id)
  }, [refreshAll])

  async function triggerCycle() {
    setCycling(true)
    setError(null)
    try {
      const r = await fetch(`${BASE}/cycle/trigger`, { method: 'POST' })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      await refreshAll()
      return data
    } catch (err) {
      setError(err.message || 'Cycle failed')
      return null
    } finally {
      setCycling(false)
    }
  }

  async function closePosition(symbol) {
    try {
      const r = await fetch(`${BASE}/positions/${symbol}/close`, { method: 'POST' })
      if (!r.ok) throw new Error('Close failed')
      await refreshAll()
      return await r.json()
    } catch (err) {
      setError(err.message)
      return null
    }
  }

  async function runBacktest(payload) {
    try {
      const r = await fetch(`${BASE}/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${r.status}`)
      }
      return await r.json()
    } catch (err) {
      throw err
    }
  }

  async function updateConfig(payload) {
    try {
      const r = await fetch(`${BASE}/config`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-Agent-Config-Update': 'yes',
        },
        body: JSON.stringify(payload),
      })
      if (!r.ok) throw new Error('Config update failed')
      await fetchStatus()
      return await r.json()
    } catch (err) {
      setError(err.message)
      return null
    }
  }

  return {
    status, decisions, trades, positions, performance,
    loading, cycling, error,
    triggerCycle, closePosition, runBacktest, updateConfig, refreshAll,
  }
}
