import { useState, useCallback } from 'react'

const BASE = '/api/v1/mf-tracker'

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (res.status === 204) return null
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`)
  return data
}

export function useMFTracker() {
  const [funds,       setFunds]       = useState([])
  const [sips,        setSips]        = useState([])
  const [loading,     setLoading]     = useState(false)
  const [sipLoading,  setSipLoading]  = useState(false)
  const [analysis,    setAnalysis]    = useState(null)
  const [analyzing,   setAnalyzing]   = useState(false)

  const loadFunds = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch(`${BASE}/funds`)
      setFunds(Array.isArray(data) ? data : [])
    } catch { setFunds([]) }
    finally  { setLoading(false) }
  }, [])

  const loadSips = useCallback(async () => {
    setSipLoading(true)
    try {
      const data = await apiFetch(`${BASE}/sips`)
      setSips(Array.isArray(data) ? data : [])
    } catch { setSips([]) }
    finally  { setSipLoading(false) }
  }, [])

  async function searchFunds(q) {
    if (!q || q.length < 2) return []
    const data = await apiFetch(`${BASE}/funds/search?q=${encodeURIComponent(q)}`)
    return Array.isArray(data) ? data : []
  }

  async function addFund(fund) {
    const data = await apiFetch(`${BASE}/funds`, {
      method: 'POST',
      body: JSON.stringify(fund),
    })
    await loadFunds()
    return data
  }

  async function removeFund(id) {
    await apiFetch(`${BASE}/funds/${id}`, { method: 'DELETE' })
    setFunds(prev => prev.filter(f => f.id !== id))
    setSips(prev => prev.filter(s => s.fund_id !== id))
  }

  async function refreshFundNav(id) {
    const data = await apiFetch(`${BASE}/funds/${id}/refresh`, { method: 'POST' })
    await loadFunds()
    return data
  }

  async function addSip(payload) {
    const data = await apiFetch(`${BASE}/sips`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    await loadSips()
    return data
  }

  async function updateSip(id, payload) {
    const data = await apiFetch(`${BASE}/sips/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    await loadSips()
    return data
  }

  async function deleteSip(id) {
    await apiFetch(`${BASE}/sips/${id}`, { method: 'DELETE' })
    setSips(prev => prev.filter(s => s.id !== id))
  }

  async function runAnalysis() {
    setAnalyzing(true)
    setAnalysis(null)
    try {
      const data = await apiFetch(`${BASE}/analysis`)
      setAnalysis(data)
    } catch (err) {
      setAnalysis({ analysis: `Analysis failed: ${err.message}` })
    } finally {
      setAnalyzing(false)
    }
  }

  return {
    funds, sips, loading, sipLoading, analysis, analyzing,
    loadFunds, loadSips,
    searchFunds, addFund, removeFund, refreshFundNav,
    addSip, updateSip, deleteSip,
    runAnalysis,
  }
}
