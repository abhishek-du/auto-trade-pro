import { useState, useEffect, useCallback } from 'react'
import { BookMarked, RefreshCw } from 'lucide-react'
import { useWatchlist }          from '../hooks/useWatchlist'
import WatchlistToolbar          from '../components/watchlist/WatchlistToolbar'
import WatchlistTableHeader      from '../components/watchlist/WatchlistTableHeader'
import WatchlistRow              from '../components/watchlist/WatchlistRow'
import WatchlistDetailPanel      from '../components/watchlist/WatchlistDetailPanel'
import WatchlistAlertsBar        from '../components/watchlist/WatchlistAlertsBar'
import ChartModal                from '../components/chart/ChartModal'
import toast                     from 'react-hot-toast'
import { apiFetch } from '../api/client'

function ISTClock({ marketStatus }) {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () =>
      setTime(new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false }))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const isOpen  = marketStatus === 'OPEN'
  const badgeCls = isOpen
    ? 'bg-profit/15 text-profit border-profit/30'
    : 'bg-muted/15 text-muted border-muted/30'

  return (
    <div className="flex items-center gap-2">
      <span className={`text-[11px] font-bold border px-2 py-0.5 rounded-full ${badgeCls}`}>
        NSE {isOpen ? 'OPEN' : 'CLOSED'}
      </span>
      <span className="text-muted text-xs tabular-nums font-mono">{time} IST</span>
    </div>
  )
}

export default function Watchlist() {
  const {
    watchlist, allWatchlist,
    connected, lastUpdated, alertsData,
    sortBy, sortDir, toggleSort,
    filterSector, setFilterSector, sectors,
    filterSignal, setFilterSignal,
    searchQuery, setSearchQuery,
  } = useWatchlist()

  const [expandedSymbol, setExpandedSymbol] = useState(null)
  const [refreshing,     setRefreshing]     = useState(false)
  const [alertFilter,    setAlertFilter]    = useState(null)
  const [marketStatus,   setMarketStatus]   = useState('CLOSED')
  const [chartSymbol,    setChartSymbol]    = useState(null)
  const [chartName,      setChartName]      = useState(null)

  const handleOpenChart = useCallback((stock) => {
    setChartSymbol(stock.symbol)
    setChartName(stock.name || stock.symbol)
  }, [])

  // Poll market status
  useEffect(() => {
    const check = () =>
      apiFetch('/api/v1/india/market-status')
        .then(d => setMarketStatus(d.nse_open ? 'OPEN' : 'CLOSED'))
        .catch(() => {})
    check()
    const id = setInterval(check, 60_000)
    return () => clearInterval(id)
  }, [])

  async function handleRefresh() {
    setRefreshing(true)
    try {
      const res  = await apiFetch('/api/v1/india/watchlist/refresh', { method: 'POST' })
      const data = await res.json()
      toast.success(`Refreshed ${data.refreshed_count} stocks in ${data.duration_ms}ms`)
    } catch {
      toast.error('Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  function handleAlertClick(key, stocks) {
    if (alertFilter?.key === key) {
      setAlertFilter(null)
    } else {
      setAlertFilter({ key, stocks })
      toast(`Showing ${stocks.length} stock(s) matching alert`, { icon: '🔔' })
    }
  }

  // Apply alert filter on top of watchlist filters
  const displayList = alertFilter
    ? watchlist.filter(s => alertFilter.stocks.some(a => a.symbol === s.symbol))
    : watchlist

  return (
    <>
    <div className="space-y-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <BookMarked size={18} className="text-cyan" />
            NSE Watchlist
          </h1>
          <p className="text-muted text-sm mt-0.5">{allWatchlist.length} stocks tracked live</p>
        </div>
        <ISTClock marketStatus={marketStatus} />
      </div>

      {/* Alerts strip */}
      {alertsData && (
        <WatchlistAlertsBar alertsData={alertsData} onAlertClick={handleAlertClick} />
      )}

      {/* Toolbar */}
      <WatchlistToolbar
        searchQuery={searchQuery}   setSearchQuery={setSearchQuery}
        filterSector={filterSector} setFilterSector={setFilterSector} sectors={sectors}
        filterSignal={filterSignal} setFilterSignal={setFilterSignal}
        connected={connected}       lastUpdated={lastUpdated}
        onRefresh={handleRefresh}   refreshing={refreshing}
        totalCount={allWatchlist.length}
        filteredCount={displayList.length}
      />

      {/* Main table */}
      <div className="glass-panel rounded-xl overflow-hidden hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] transition-all duration-300">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <WatchlistTableHeader sortBy={sortBy} sortDir={sortDir} onSort={toggleSort} />
            <tbody>
              {displayList.length === 0 ? (
                <tr>
                  <td colSpan={10} className="text-center py-16 text-muted text-sm">
                    <div className="flex flex-col items-center gap-3">
                      <span className="text-3xl">🔍</span>
                      <span>No stocks match your filters</span>
                      <button
                        onClick={() => {
                          setSearchQuery('')
                          setFilterSector('All')
                          setFilterSignal('All')
                          setAlertFilter(null)
                        }}
                        className="text-accent text-xs hover:underline"
                      >
                        Clear filters
                      </button>
                    </div>
                  </td>
                </tr>
              ) : (
                displayList.map(stock => (
                  <>
                    <WatchlistRow
                      key={stock.symbol}
                      stock={stock}
                      isExpanded={expandedSymbol === stock.symbol}
                      onExpand={() =>
                        setExpandedSymbol(expandedSymbol === stock.symbol ? null : stock.symbol)
                      }
                      onChart={handleOpenChart}
                    />
                    {expandedSymbol === stock.symbol && (
                      <tr key={`${stock.symbol}-detail`}>
                        <td colSpan={11} className="p-0">
                          <WatchlistDetailPanel
                            stock={stock}
                            onClose={() => setExpandedSymbol(null)}
                          />
                        </td>
                      </tr>
                    )}
                  </>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-white/5 bg-black/20 flex items-center justify-between flex-wrap gap-2">
          <span className="text-muted text-xs">
            Showing {displayList.length} of {allWatchlist.length} stocks
            {alertFilter ? ` · Alert: ${alertFilter.key.replace(/_/g, ' ')}` : ''}
          </span>
          <span className={`text-xs font-medium flex items-center gap-1.5 ${connected ? 'text-profit' : 'text-muted'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-profit animate-pulse' : 'bg-muted'}`} />
            {connected ? 'Live via WebSocket' : 'Polling (reconnecting…)'}
          </span>
        </div>
      </div>
    </div>

    <ChartModal
      symbol={chartSymbol}
      name={chartName}
      isOpen={!!chartSymbol}
      onClose={() => setChartSymbol(null)}
    />
    </>
  )
}
