import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../api/client'

export const ASSET_CLASSES = {
  large_cap:     { label: 'Large Cap',     color: '#3B82F6', emoji: '🏢' },
  mid_cap:       { label: 'Mid Cap',       color: '#8B5CF6', emoji: '🏬' },
  small_cap:     { label: 'Small Cap',     color: '#EC4899', emoji: '🏠' },
  debt:          { label: 'Debt',          color: '#10B981', emoji: '📜' },
  gold:          { label: 'Gold',          color: '#F59E0B', emoji: '🪙' },
  international: { label: 'International', color: '#06B6D4', emoji: '🌐' },
  cash:          { label: 'Cash / Liquid', color: '#6B7280', emoji: '💵' },
  other:         { label: 'Other',         color: '#94A3B8', emoji: '•'  },
}

export function useAllocation(portfolioId, sipGoalIds = []) {
  const [analysis,              setAnalysis]              = useState(null)
  const [loading,               setLoading]               = useState(false)
  const [error,                 setError]                 = useState(null)
  const [riskProfile,           setRiskProfile]           = useState('moderate')
  const [rebalancingThreshold,  setRebalancingThreshold]  = useState(5)
  const [newInvestment,         setNewInvestment]         = useState(0)
  const [customTarget,          setCustomTarget]          = useState(null)
  const [age,                   setAge]                   = useState(null)

  const loadAnalysis = useCallback(async () => {
    if (!portfolioId && sipGoalIds.length === 0) { setAnalysis(null); return }
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ risk_profile: riskProfile, rebalancing_threshold: rebalancingThreshold, new_investment: newInvestment })
      if (portfolioId) params.set('portfolio_id', portfolioId)
      if (age)         params.set('age', age)
      sipGoalIds.forEach(id => params.append('sip_goal_ids', id))
      const res  = await apiFetch(`/api/v1/allocation/analysis?${params}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed to load analysis')
      setAnalysis(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [portfolioId, JSON.stringify(sipGoalIds), riskProfile, rebalancingThreshold, newInvestment, age])

  useEffect(() => { loadAnalysis() }, [loadAnalysis])

  async function submitQuestionnaire(answers) {
    const result = await apiFetch('/api/v1/allocation/risk-profile', {
      method: 'POST',
      body:   JSON.stringify(answers),
    })
    setRiskProfile(result.profile)
    return result
  }

  function applyCustomTarget(target) {
    setCustomTarget(target)
  }

  const effectiveTarget = customTarget || analysis?.target_allocation || {}

  return {
    analysis, loading, error, loadAnalysis,
    riskProfile, setRiskProfile,
    rebalancingThreshold, setRebalancingThreshold,
    newInvestment, setNewInvestment,
    age, setAge,
    effectiveTarget, customTarget, applyCustomTarget, setCustomTarget,
    submitQuestionnaire,
  }
}
