import { Search, X, RefreshCw, Wifi, WifiOff } from 'lucide-react'
import { timeSince } from '../../utils/indianFormat'

const SIGNAL_FILTERS = ['All', 'BUY', 'SELL', 'HOLD']
const SIGNAL_COLORS  = { BUY: 'text-profit border-profit/40 bg-profit/10', SELL: 'text-loss border-loss/40 bg-loss/10', HOLD: 'text-warn border-warn/40 bg-warn/10' }

export default function WatchlistToolbar({
  searchQuery, setSearchQuery,
  filterSector, setFilterSector, sectors,
  filterSignal, setFilterSignal,
  connected, lastUpdated,
  onRefresh, refreshing,
  totalCount, filteredCount,
}) {
  const visibleSectors = sectors.slice(0, 6)
  const extraSectors   = sectors.slice(6)

  return (
    <div className="glass-panel border border-border rounded-xl px-4 py-3 space-y-3">
      {/* Row 1 — search + status + refresh */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Search */}
        <div className="relative flex-1 min-w-48 max-w-64">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search stocks…"
            className="w-full bg-surface border border-border rounded-lg pl-8 pr-8 py-2 text-xs text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent/50"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-slate-300">
              <X size={12} />
            </button>
          )}
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Count */}
        <span className="text-muted text-xs shrink-0">
          Showing <span className="text-slate-300 font-medium">{filteredCount}</span> of {totalCount}
        </span>

        {/* WS status */}
        <span className={`flex items-center gap-1.5 text-xs font-medium shrink-0 ${connected ? 'text-profit' : 'text-muted'}`}>
          {connected
            ? <><span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse shrink-0" />Live</>
            : <><WifiOff size={11} />Polling</>}
        </span>

        {/* Last updated */}
        {lastUpdated && (
          <span className="text-muted text-xs shrink-0">{timeSince(lastUpdated.toISOString())}</span>
        )}

        {/* Refresh */}
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-slate-300 hover:text-white hover:bg-white/5 disabled:opacity-50 transition-all"
        >
          <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Row 2 — sector pills + signal filter */}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Sector pills */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {visibleSectors.map(s => (
            <button
              key={s}
              onClick={() => setFilterSector(s)}
              className={[
                'px-2.5 py-1 rounded-full text-[11px] font-medium border transition-all',
                filterSector === s
                  ? 'bg-accent border-accent text-white'
                  : 'border-border text-muted hover:text-slate-300 hover:border-slate-500',
              ].join(' ')}
            >
              {s}
            </button>
          ))}
          {extraSectors.length > 0 && (
            <div className="relative group">
              <button className="px-2.5 py-1 rounded-full text-[11px] font-medium border border-border text-muted hover:text-slate-300">
                +{extraSectors.length} more
              </button>
              <div className="absolute top-full left-0 mt-1 glass-panel border border-border rounded-lg p-2 z-20 hidden group-hover:flex flex-col gap-1 min-w-24 shadow-lg">
                {extraSectors.map(s => (
                  <button
                    key={s}
                    onClick={() => setFilterSector(s)}
                    className={`px-2 py-1 rounded text-[11px] text-left transition-colors ${filterSector === s ? 'text-accent' : 'text-muted hover:text-slate-300'}`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Divider */}
        <div className="w-px h-4 bg-border shrink-0" />

        {/* Signal filter */}
        <div className="flex items-center gap-1">
          {SIGNAL_FILTERS.map(f => (
            <button
              key={f}
              onClick={() => setFilterSignal(f)}
              className={[
                'px-2.5 py-1 rounded-md text-[11px] font-semibold border transition-all',
                filterSignal === f
                  ? (f === 'All' ? 'bg-accent/20 border-accent/40 text-accent' : SIGNAL_COLORS[f])
                  : 'border-transparent text-muted hover:text-slate-300',
              ].join(' ')}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
