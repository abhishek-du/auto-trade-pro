import { useState, useCallback } from 'react'

const BASE = '/api/v1/sip'

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

export function useSIPTracker() {
  const [goals,        setGoals]        = useState([])
  const [activeGoal,   setActiveGoal]   = useState(null)
  const [loading,      setLoading]      = useState(false)
  const [detailLoading,setDetailLoading]= useState(false)

  const loadGoals = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch(`${BASE}/goals`)
      setGoals(Array.isArray(data) ? data : [])
    } catch { setGoals([]) }
    finally  { setLoading(false) }
  }, [])

  const loadGoalDetail = useCallback(async (goalId) => {
    setDetailLoading(true)
    try {
      const data = await apiFetch(`${BASE}/goals/${goalId}`)
      setActiveGoal(data)
      return data
    } catch { return null }
    finally  { setDetailLoading(false) }
  }, [])

  async function createGoal(payload) {
    const data = await apiFetch(`${BASE}/goals`, { method: 'POST', body: JSON.stringify(payload) })
    await loadGoals()
    return data
  }

  async function updateGoal(id, payload) {
    const data = await apiFetch(`${BASE}/goals/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
    await loadGoals()
    if (activeGoal?.goal_id === id) await loadGoalDetail(id)
    return data
  }

  async function deleteGoal(id) {
    await apiFetch(`${BASE}/goals/${id}`, { method: 'DELETE' })
    setGoals(prev => prev.filter(g => g.id !== id))
    if (activeGoal?.goal_id === id) setActiveGoal(null)
  }

  async function addFund(goalId, payload) {
    const data = await apiFetch(`${BASE}/goals/${goalId}/funds`, { method: 'POST', body: JSON.stringify(payload) })
    await loadGoalDetail(goalId)
    await loadGoals()
    return data
  }

  async function removeFund(goalId, fundId) {
    await apiFetch(`${BASE}/goals/${goalId}/funds/${fundId}`, { method: 'DELETE' })
    await loadGoalDetail(goalId)
    await loadGoals()
  }

  async function addInstallment(goalId, payload) {
    const data = await apiFetch(`${BASE}/goals/${goalId}/installments`, { method: 'POST', body: JSON.stringify(payload) })
    await loadGoalDetail(goalId)
    await loadGoals()
    return data
  }

  async function getInstallments(goalId) {
    return apiFetch(`${BASE}/goals/${goalId}/installments`)
  }

  async function getProjection(goalId) {
    return apiFetch(`${BASE}/goals/${goalId}/projection`)
  }

  async function refreshNavs(goalId) {
    const data = await apiFetch(`${BASE}/goals/${goalId}/refresh`, { method: 'POST' })
    setActiveGoal(data)
    await loadGoals()
    return data
  }

  async function runCalculator(payload) {
    return apiFetch(`${BASE}/calculator`, { method: 'POST', body: JSON.stringify(payload) })
  }

  async function calcRequiredSIP(payload) {
    return apiFetch(`${BASE}/calculator/required-sip`, { method: 'POST', body: JSON.stringify(payload) })
  }

  async function calcTimeToTarget(payload) {
    return apiFetch(`${BASE}/calculator/time-to-target`, { method: 'POST', body: JSON.stringify(payload) })
  }

  async function searchFunds(q) {
    if (!q || q.length < 2) return []
    return apiFetch(`${BASE}/funds/search?q=${encodeURIComponent(q)}`)
  }

  return {
    goals, activeGoal, loading, detailLoading,
    loadGoals, loadGoalDetail,
    createGoal, updateGoal, deleteGoal,
    addFund, removeFund,
    addInstallment, getInstallments, getProjection, refreshNavs,
    runCalculator, calcRequiredSIP, calcTimeToTarget,
    searchFunds,
  }
}
