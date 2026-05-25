import { useState, useEffect, useCallback } from 'react'

const BASE = '/api/v1/tax'

async function apiFetch(url) {
  const res = await fetch(url)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

export function useTaxCalculator(portfolioId) {
  const [financialYear,    setFinancialYear]    = useState('FY2025-26')
  const [annualIncome,     setAnnualIncome]     = useState(1000000)
  const [alreadyUsedLTCG, setAlreadyUsedLTCG]  = useState(0)
  const [taxSummary,       setTaxSummary]       = useState(null)
  const [breakdown,        setBreakdown]        = useState(null)
  const [harvesting,       setHarvesting]       = useState(null)
  const [loading,          setLoading]          = useState(false)
  const [error,            setError]            = useState('')
  const [availableFYs,     setAvailableFYs]     = useState(['FY2025-26'])

  // Load available financial years when portfolio changes
  useEffect(() => {
    if (!portfolioId) return
    apiFetch(`${BASE}/financial-years/${portfolioId}`)
      .then(data => setAvailableFYs(Array.isArray(data) ? data : ['FY2025-26']))
      .catch(() => {})
  }, [portfolioId])

  const loadTaxData = useCallback(async () => {
    if (!portfolioId) return
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({
        financial_year:    financialYear,
        annual_income:     annualIncome,
        already_used_ltcg: alreadyUsedLTCG,
      })
      const [sum, brk, harv] = await Promise.all([
        apiFetch(`${BASE}/summary/${portfolioId}?${params}`),
        apiFetch(`${BASE}/breakdown/${portfolioId}?financial_year=${financialYear}`),
        apiFetch(`${BASE}/harvesting/${portfolioId}?financial_year=${financialYear}&annual_income=${annualIncome}`),
      ])
      setTaxSummary(sum)
      setBreakdown(brk)
      setHarvesting(harv)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [portfolioId, financialYear, annualIncome, alreadyUsedLTCG])

  useEffect(() => {
    if (portfolioId) loadTaxData()
  }, [portfolioId, financialYear, annualIncome, alreadyUsedLTCG])

  return {
    financialYear, setFinancialYear, availableFYs,
    annualIncome,  setAnnualIncome,
    alreadyUsedLTCG, setAlreadyUsedLTCG,
    taxSummary, breakdown, harvesting,
    loading, error,
    refresh: loadTaxData,
  }
}
