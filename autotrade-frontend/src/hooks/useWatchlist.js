import { useState, useEffect, useRef, useMemo } from 'react'
import { useLiveMarket } from './useLiveMarket'

export function useWatchlist() {
  const { prices, connected, lastUpdated } = useLiveMarket()

  const [watchlist,     setWatchlist]     = useState([])
  const [alertsData,    setAlertsData]    = useState(null)
  const [sortBy,        setSortBy]        = useState('name')
  const [sortDir,       setSortDir]       = useState('asc')
  const [filterSector,  setFilterSector]  = useState('All')
  const [filterSignal,  setFilterSignal]  = useState('All')
  const [searchQuery,   setSearchQuery]   = useState('')

  const prevPricesRef = useRef({})

  // ── Initial REST load ──────────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/v1/india/watchlist')
      .then(r => r.json())
      .then(data => setWatchlist(data.stocks || []))
      .catch(() => {})

    fetch('/api/v1/india/watchlist/alerts')
      .then(r => r.json())
      .then(setAlertsData)
      .catch(() => {})
  }, [])

  // ── Merge live WebSocket price ticks into watchlist ───────────────────────
  useEffect(() => {
    if (!prices || Object.keys(prices).length === 0) return

    setWatchlist(prev => prev.map(stock => {
      const live = prices[stock.symbol]
      if (!live) return stock
      const prevPrice = prevPricesRef.current[stock.symbol]?.price ?? live.price
      return {
        ...stock,
        price:      live.price,
        change:     live.change,
        change_pct: live.change_pct,
        high:       live.high,
        low:        live.low,
        volume:     live.volume,
        last_updated: live.last_updated,
        _priceDirection:
          live.price > prevPrice ? 'up' :
          live.price < prevPrice ? 'down' : null,
      }
    }))

    prevPricesRef.current = prices
  }, [prices])

  // ── Filtered + sorted list ─────────────────────────────────────────────────
  const filteredWatchlist = useMemo(() => {
    let list = [...watchlist]

    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      list = list.filter(s =>
        s.symbol.toLowerCase().includes(q) ||
        (s.name || '').toLowerCase().includes(q)
      )
    }

    if (filterSector !== 'All')
      list = list.filter(s => s.sector === filterSector)

    if (filterSignal !== 'All')
      list = list.filter(s => s.signal === filterSignal)

    list.sort((a, b) => {
      let aVal = a[sortBy] ?? (typeof a[sortBy] === 'string' ? '' : 0)
      let bVal = b[sortBy] ?? (typeof b[sortBy] === 'string' ? '' : 0)
      if (typeof aVal === 'string') aVal = aVal.toLowerCase()
      if (typeof bVal === 'string') bVal = bVal.toLowerCase()
      if (aVal < bVal) return sortDir === 'asc' ? -1 : 1
      if (aVal > bVal) return sortDir === 'asc' ? 1 : -1
      return 0
    })

    return list
  }, [watchlist, sortBy, sortDir, filterSector, filterSignal, searchQuery])

  const sectors = useMemo(() =>
    ['All', ...new Set(watchlist.map(s => s.sector).filter(Boolean))],
    [watchlist]
  )

  function toggleSort(field) {
    if (sortBy === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortBy(field); setSortDir('desc') }
  }

  return {
    watchlist: filteredWatchlist,
    allWatchlist: watchlist,
    connected, lastUpdated, alertsData,
    sortBy, sortDir, toggleSort,
    filterSector, setFilterSector, sectors,
    filterSignal, setFilterSignal,
    searchQuery, setSearchQuery,
  }
}
