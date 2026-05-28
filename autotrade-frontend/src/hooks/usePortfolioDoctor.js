import { useState, useEffect } from 'react'

const BASE = '/api/v1/doctor'

export function usePortfolioDoctor(portfolioId) {
  const [diagnosis,    setDiagnosis]    = useState(null)
  const [history,      setHistory]      = useState([])
  const [loading,      setLoading]      = useState(false)
  const [progress,     setProgress]     = useState('')
  const [error,        setError]        = useState(null)
  const [riskProfile,  setRiskProfile]  = useState('moderate')
  const [annualIncome, setAnnualIncome] = useState(1000000)

  useEffect(() => {
    if (!portfolioId) return
    fetch(`${BASE}/diagnose/${portfolioId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setDiagnosis(d) })
      .catch(() => {})
    fetch(`${BASE}/history/${portfolioId}`)
      .then(r => r.ok ? r.json() : [])
      .then(d => setHistory(Array.isArray(d) ? d : []))
      .catch(() => {})
  }, [portfolioId])

  async function runDiagnosis() {
    if (!portfolioId) return
    setLoading(true)
    setError(null)

    const steps = [
      'Fetching portfolio data...',
      'Analysing fundamentals...',
      'Running tax calculations...',
      'Checking sector allocation...',
      'Generating AI narrative...',
    ]
    let step = 0
    setProgress(steps[0])
    const interval = setInterval(() => {
      step = Math.min(step + 1, steps.length - 1)
      setProgress(steps[step])
    }, 5000)

    try {
      const r = await fetch(`${BASE}/diagnose`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          portfolio_id:  portfolioId,
          risk_profile:  riskProfile,
          annual_income: annualIncome,
        }),
      })
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        throw new Error(err.detail || 'Diagnosis failed')
      }
      const data = await r.json()
      setDiagnosis(data)
      fetch(`${BASE}/history/${portfolioId}`)
        .then(r => r.ok ? r.json() : [])
        .then(d => setHistory(Array.isArray(d) ? d : []))
        .catch(() => {})
    } catch (err) {
      setError(err.message || 'Diagnosis failed. Please try again.')
    } finally {
      clearInterval(interval)
      setLoading(false)
      setProgress('')
    }
  }

  const criticalCount = diagnosis?.findings?.filter(f => f.severity === 'CRITICAL').length || 0
  const warningCount  = diagnosis?.findings?.filter(f => f.severity === 'WARNING').length  || 0
  const goodCount     = diagnosis?.findings?.filter(f => f.severity === 'GOOD').length     || 0

  return {
    diagnosis, history, loading, progress, error,
    riskProfile, setRiskProfile,
    annualIncome, setAnnualIncome,
    runDiagnosis,
    criticalCount, warningCount, goodCount,
  }
}
