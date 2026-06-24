import { useEffect, useState } from 'react'
import AdvanceDeclineBar from './AdvanceDeclineBar'
import MarketMoodBadge   from './MarketMoodBadge'
import { formatPct }     from '../../utils/indianFormat'
import { apiFetch } from '../../api/client'

function useBreadthSummary() {
  const [data, setData] = useState(null)
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/india/breadth/summary')
        .then(setData)
        .catch(() => {})
    load()
    const id = setInterval(load, 120_000)
    return () => clearInterval(id)
  }, [])
  return data
}

function MiniStockRow({ stock, isGain }) {
  if (!stock) return null
  const pct = stock.change_pct || 0
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-slate-300 text-[11px] font-semibold">{(stock.symbol || '').replace('.NS', '')}</span>
      <span className={`text-[11px] font-bold tabular-nums ${isGain ? 'text-profit' : 'text-loss'}`}>
        {formatPct(pct)}
      </span>
    </div>
  )
}

export function BreadthCompact() {
  const data = useBreadthSummary()
  if (!data) return null

  const { nse_advances: adv = 0, nse_declines: dec = 0, nse_unchanged: unc = 0, nse_market_mood: mood = 'NEUTRAL' } = data
  const moodColors = {
    STRONGLY_BULLISH: 'text-emerald-400',
    BULLISH:          'text-profit',
    NEUTRAL:          'text-slate-400',
    BEARISH:          'text-loss',
    STRONGLY_BEARISH: 'text-red-400',
  }

  return (
    <div className="flex items-center gap-3 text-xs font-medium flex-wrap">
      <span className="text-profit">▲ {adv.toLocaleString('en-IN')} Advances</span>
      <span className="text-muted">·</span>
      <span className="text-loss">▼ {dec.toLocaleString('en-IN')} Declines</span>
      {unc > 0 && <><span className="text-muted">·</span><span className="text-muted">— {unc} Unchanged</span></>}
      <span className="text-muted">·</span>
      <span className={`font-bold ${moodColors[mood] || 'text-slate-400'}`}>
        Mood: {(mood || 'NEUTRAL').replace('_', ' ')}
      </span>
    </div>
  )
}

export function BreadthWidget({ compact = false }) {
  const [breadth, setBreadth] = useState(null)

  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/india/breadth')
        .then(setBreadth)
        .catch(() => {})
    load()
    const id = setInterval(load, 120_000)
    return () => clearInterval(id)
  }, [])

  if (compact) return <BreadthCompact />
  if (!breadth) return null

  const nse      = breadth.nse      || {}
  const wl       = breadth.watchlist || {}
  const gainers  = (breadth.top_gainers || []).slice(0, 3)
  const losers   = (breadth.top_losers  || []).slice(0, 3)

  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-slate-200 text-sm font-semibold">Market Breadth</h3>
        <MarketMoodBadge mood={nse.market_mood || 'NEUTRAL'} size="sm" />
      </div>

      <AdvanceDeclineBar
        advances={nse.advances   || 0}
        declines={nse.declines   || 0}
        unchanged={nse.unchanged || 0}
        total={nse.total}
        label="NSE"
      />

      {/* Mini gainers/losers */}
      <div className="grid grid-cols-2 gap-4 pt-1">
        <div>
          <div className="text-[10px] text-profit font-semibold uppercase tracking-wider mb-1">Top Gainers</div>
          {gainers.map(s => <MiniStockRow key={s.symbol} stock={s} isGain />)}
          {gainers.length === 0 && <div className="text-muted text-[10px]">No data</div>}
        </div>
        <div>
          <div className="text-[10px] text-loss font-semibold uppercase tracking-wider mb-1">Top Losers</div>
          {losers.map(s => <MiniStockRow key={s.symbol} stock={s} isGain={false} />)}
          {losers.length === 0 && <div className="text-muted text-[10px]">No data</div>}
        </div>
      </div>

      <div className="text-[10px] text-muted pt-1 border-t border-border">
        Watchlist: {wl.advances || 0} adv / {wl.declines || 0} dec of {wl.total || 0} stocks
        {breadth.source && (
          <span className={`ml-2 ${breadth.source === 'MIXED' ? 'text-profit' : 'text-muted'}`}>
            · {breadth.source === 'MIXED' ? 'NSE Live' : 'Watchlist Only'}
          </span>
        )}
      </div>
    </div>
  )
}

export default BreadthWidget
