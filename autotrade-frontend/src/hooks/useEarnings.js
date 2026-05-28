import { useState, useEffect } from 'react'

const BASE = '/api/v1/earnings'

export function useEarnings(symbol) {
  const [summary,          setSummary]          = useState(null)
  const [availableList,    setAvailableList]     = useState([])
  const [selectedQuarter,  setSelectedQuarter]  = useState(null)
  const [loading,          setLoading]          = useState(false)
  const [error,            setError]            = useState(null)
  const [progressMsg,      setProgressMsg]      = useState('')
  const [compareMode,      setCompareMode]      = useState(false)
  const [compareSummaries, setCompareSummaries] = useState([])

  const PROGRESS_STEPS = [
    'Searching BSE filings...',
    'Downloading earnings transcript PDF...',
    'Extracting text from PDF...',
    'Analysing with Groq AI...',
    'Generating structured summary...',
  ]

  useEffect(() => {
    if (!symbol) return
    fetchAvailableList()
    fetchSummary(null, false)
  }, [symbol])

  async function fetchAvailableList() {
    try {
      const r = await fetch(`${BASE}/list/${encodeURIComponent(symbol)}`)
      const d = r.ok ? await r.json() : []
      setAvailableList(Array.isArray(d) ? d : [])
    } catch { setAvailableList([]) }
  }

  async function fetchSummary(quarter = null, refresh = false) {
    if (!symbol) return
    setLoading(true)
    setError(null)

    // Rotate progress messages
    let step = 0
    setProgressMsg(PROGRESS_STEPS[0])
    const interval = setInterval(() => {
      step = Math.min(step + 1, PROGRESS_STEPS.length - 1)
      setProgressMsg(PROGRESS_STEPS[step])
    }, 5000)

    try {
      const params = new URLSearchParams()
      if (quarter) params.set('quarter', quarter)
      if (refresh)  params.set('refresh', 'true')
      const r = await fetch(`${BASE}/summary/${encodeURIComponent(symbol)}?${params}`)
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${r.status}`)
      }
      const data = await r.json()
      setSummary(data)
      setSelectedQuarter(data.quarter)
    } catch (err) {
      setError(err.message || 'Failed to fetch earnings summary')
    } finally {
      clearInterval(interval)
      setLoading(false)
      setProgressMsg('')
    }
  }

  async function loadComparison(quarters) {
    try {
      const params = new URLSearchParams()
      quarters.forEach(q => params.append('quarters', q))
      const r = await fetch(`${BASE}/compare/${encodeURIComponent(symbol)}?${params}`)
      const data = r.ok ? await r.json() : []
      setCompareSummaries(data)
      setCompareMode(true)
    } catch { /* ignore */ }
  }

  async function refreshSummary(quarter) {
    setLoading(true)
    setError(null)
    let step = 0
    setProgressMsg(PROGRESS_STEPS[0])
    const interval = setInterval(() => {
      step = Math.min(step + 1, PROGRESS_STEPS.length - 1)
      setProgressMsg(PROGRESS_STEPS[step])
    }, 5000)
    try {
      const params = new URLSearchParams()
      if (quarter) params.set('quarter', quarter)
      const r = await fetch(`${BASE}/refresh/${encodeURIComponent(symbol)}?${params}`, { method: 'POST' })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setSummary(data)
      await fetchAvailableList()
    } catch (err) {
      setError(err.message || 'Refresh failed')
    } finally {
      clearInterval(interval)
      setLoading(false)
      setProgressMsg('')
    }
  }

  return {
    summary, availableList, selectedQuarter,
    loading, error, progressMsg,
    compareMode, compareSummaries,
    fetchSummary, loadComparison, refreshSummary, setCompareMode,
  }
}
