import { useState, useEffect, useCallback, useRef } from 'react'
import { useLiveMarket } from './useLiveMarket'

const BASE = '/api/v1/portfolios'

export function usePortfolioTracker() {
  const [portfolios, setPortfolios]       = useState([])
  const [activeId, setActiveId]           = useState(null)
  const [detail, setDetail]               = useState(null)   // full summary for active portfolio
  const [loading, setLoading]             = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const { prices }                        = useLiveMarket()
  const pricesRef                         = useRef(prices)

  useEffect(() => { pricesRef.current = prices }, [prices])

  // ── Load portfolio list ─────────────────────────────────────────────────
  const loadPortfolios = useCallback(async () => {
    try {
      const res  = await fetch(BASE + '/')
      const data = await res.json()
      setPortfolios(Array.isArray(data) ? data : [])
      if (!activeId && data.length > 0) {
        setActiveId(data[0].id)
      }
    } catch {
      setPortfolios([])
    } finally {
      setLoading(false)
    }
  }, [activeId])

  // ── Load active portfolio detail ────────────────────────────────────────
  const loadDetail = useCallback(async (id) => {
    if (!id) return
    setDetailLoading(true)
    try {
      const res  = await fetch(`${BASE}/${id}`)
      const data = await res.json()
      setDetail(data)
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => { loadPortfolios() }, [])
  useEffect(() => { if (activeId) loadDetail(activeId) }, [activeId])

  // ── Apply live prices to holdings without refetch ───────────────────────
  const detailWithLivePrices = detail ? {
    ...detail,
    holdings: (detail.holdings || []).map(h => {
      const liveEntry = pricesRef.current[h.symbol]
      if (!liveEntry) return h
      const ltp        = liveEntry.price   || h.avg_buy_price
      const cur_val    = ltp * h.quantity
      const pnl        = cur_val - h.invested
      const pnl_pct    = h.invested > 0 ? pnl / h.invested * 100 : 0
      const day_change = liveEntry.change     || 0
      const day_chg_p  = liveEntry.change_pct || 0
      return {
        ...h,
        current_price:  ltp,
        current_value:  Math.round(cur_val * 100) / 100,
        pnl:            Math.round(pnl * 100) / 100,
        pnl_pct:        Math.round(pnl_pct * 100) / 100,
        day_change,
        day_change_pct: day_chg_p,
        day_pnl:        Math.round(day_change * h.quantity * 100) / 100,
      }
    }),
  } : null

  // ── Re-derive summary from live holdings ────────────────────────────────
  const liveSummary = (() => {
    if (!detailWithLivePrices?.holdings?.length) return detail?.summary || null
    const h    = detailWithLivePrices.holdings
    const inv  = h.reduce((s, x) => s + (x.invested || 0), 0)
    const cur  = h.reduce((s, x) => s + (x.current_value || 0), 0)
    const tdp  = h.reduce((s, x) => s + (x.day_pnl || 0), 0)
    return {
      ...detail.summary,
      current_value:  Math.round(cur * 100) / 100,
      total_pnl:      Math.round((cur - inv) * 100) / 100,
      total_pnl_pct:  inv > 0 ? Math.round((cur - inv) / inv * 10000) / 100 : 0,
      today_pnl:      Math.round(tdp * 100) / 100,
    }
  })()

  // ── Actions ─────────────────────────────────────────────────────────────
  async function createPortfolio(name, description = '') {
    const res  = await fetch(BASE + '/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Failed to create portfolio')
    await loadPortfolios()
    setActiveId(data.id)
    return data
  }

  async function deletePortfolio(id) {
    const res = await fetch(`${BASE}/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete portfolio')
    if (activeId === id) setActiveId(null)
    await loadPortfolios()
    setDetail(null)
  }

  async function addHolding(payload) {
    const res = await fetch(`${BASE}/${activeId}/holdings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to add holding')
    }
    await loadDetail(activeId)
    await loadPortfolios()
  }

  async function sellHolding(holdingId, payload) {
    const res = await fetch(`${BASE}/${activeId}/holdings/${holdingId}/sell`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to sell')
    }
    await loadDetail(activeId)
    await loadPortfolios()
  }

  async function deleteHolding(holdingId) {
    await fetch(`${BASE}/${activeId}/holdings/${holdingId}`, { method: 'DELETE' })
    await loadDetail(activeId)
    await loadPortfolios()
  }

  async function searchStocks(q) {
    if (!q || q.length < 1) return []
    const res  = await fetch(`${BASE}/search/stocks?q=${encodeURIComponent(q)}`)
    return res.json()
  }

  async function getTransactions(symbol) {
    const url  = `${BASE}/${activeId}/transactions` + (symbol ? `?symbol=${symbol}` : '')
    const res  = await fetch(url)
    return res.json()
  }

  return {
    portfolios, activeId, setActiveId,
    detail:    detailWithLivePrices,
    summary:   liveSummary,
    loading,   detailLoading,
    reload:    () => loadDetail(activeId),
    createPortfolio, deletePortfolio,
    addHolding, sellHolding, deleteHolding,
    searchStocks, getTransactions,
  }
}
