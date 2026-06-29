import { Activity, RefreshCw } from 'lucide-react'
import { useBreadth }           from '../hooks/useBreadth'
import AdvanceDeclineBar        from '../components/breadth/AdvanceDeclineBar'
import MarketMoodBadge          from '../components/breadth/MarketMoodBadge'
import GainersLosersTable       from '../components/breadth/GainersLosersTable'
import Week52Panel              from '../components/breadth/Week52Panel'
import SectorBreadthTable       from '../components/breadth/SectorBreadthTable'
import BreadthHistory           from '../components/breadth/BreadthHistory'
import { timeSince }            from '../utils/indianFormat'

function SkeletonBar() {
  return <div className="h-7 rounded-md bg-slate-800/60 animate-pulse" />
}

function MoodCard({ title, subtitle, mood, loading }) {
  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-2">
      <div className="text-muted text-xs font-semibold uppercase tracking-wider">{title}</div>
      {loading
        ? <div className="h-6 w-32 rounded bg-slate-800/60 animate-pulse" />
        : <MarketMoodBadge mood={mood || 'NEUTRAL'} />
      }
      <div className="text-muted text-[11px]">{subtitle}</div>
    </div>
  )
}

export default function MarketBreadth() {
  const { breadth, loading, history, refresh } = useBreadth()

  const nse = breadth?.nse      || {}
  const bse = breadth?.bse      || {}
  const wl  = breadth?.watchlist || {}

  const sourceLabel = breadth?.source === 'MIXED' ? 'NSE Live Data' : 'Watchlist Only'
  const sourceCls   = breadth?.source === 'MIXED' ? 'text-profit' : 'text-muted'

  return (
    <div className="space-y-5">

      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <Activity size={18} className="text-cyan" />
            Market Breadth
          </h1>
          <p className="text-muted text-sm mt-0.5">NSE advances / declines, 52-week movers &amp; top stocks</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {breadth?.last_updated && (
            <span className="text-muted text-xs">{timeSince(breadth.last_updated)}</span>
          )}
          <span className={`text-xs font-semibold ${sourceCls}`}>● {sourceLabel}</span>
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs font-medium text-slate-300 hover:text-white hover:border-accent/40 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Mood cards ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <MoodCard
          title="NSE Overall"
          subtitle={`${(nse.advances || 0).toLocaleString('en-IN')} of ${(nse.total || 0).toLocaleString('en-IN')} stocks advancing`}
          mood={nse.market_mood}
          loading={loading}
        />
        <MoodCard
          title="BSE Overall"
          subtitle={bse.total ? `${(bse.advances || 0).toLocaleString('en-IN')} of ${(bse.total || 0).toLocaleString('en-IN')} stocks advancing` : 'Data not available'}
          mood={bse.market_mood || 'NEUTRAL'}
          loading={loading}
        />
        <MoodCard
          title="Prajna Watchlist"
          subtitle={`${wl.advances || 0} of ${wl.total || 0} stocks advancing`}
          mood={wl.market_mood}
          loading={loading}
        />
      </div>

      {/* ── A/D Bars ──────────────────────────────────────────────── */}
      <div className="glass-panel border border-border rounded-xl p-5 space-y-5">
        <h2 className="text-slate-200 text-sm font-semibold">Advance / Decline Distribution</h2>
        {loading ? (
          <div className="space-y-4">
            <SkeletonBar />
            <SkeletonBar />
            <SkeletonBar />
          </div>
        ) : (
          <div className="space-y-5">
            <AdvanceDeclineBar
              label="NSE All Stocks"
              advances={nse.advances   || 0}
              declines={nse.declines   || 0}
              unchanged={nse.unchanged || 0}
              total={nse.total}
            />
            {bse.total > 0 && (
              <AdvanceDeclineBar
                label="BSE All Stocks"
                advances={bse.advances   || 0}
                declines={bse.declines   || 0}
                unchanged={bse.unchanged || 0}
                total={bse.total}
              />
            )}
            <AdvanceDeclineBar
              label="Prajna Watchlist (35 stocks)"
              advances={wl.advances   || 0}
              declines={wl.declines   || 0}
              unchanged={wl.unchanged || 0}
              total={wl.total}
            />
          </div>
        )}
      </div>

      {/* ── Gainers / 52W Movers ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3" style={{ minHeight: 340 }}>
          <GainersLosersTable
            gainers={breadth?.top_gainers || []}
            losers={breadth?.top_losers   || []}
            mostActive={breadth?.most_active || []}
            source={breadth?.source}
          />
        </div>
        <div className="lg:col-span-2" style={{ minHeight: 340 }}>
          <Week52Panel
            week52High={breadth?.week52_high || []}
            week52Low={breadth?.week52_low   || []}
          />
        </div>
      </div>

      {/* ── Sector breadth table ──────────────────────────────────── */}
      {nse.by_index && Object.keys(nse.by_index).length > 0 && (
        <SectorBreadthTable byIndex={nse.by_index} />
      )}

      {/* ── Intraday history chart ─────────────────────────────────── */}
      <BreadthHistory historyData={history} />

      {/* Market closed notice */}
      {breadth && !loading && nse.total === 0 && (
        <div className="glass-panel border border-border rounded-xl px-5 py-4">
          <div className="text-center text-muted text-sm">
            <span className="font-semibold text-slate-400">Market data computed from last available prices.</span>
            <br />
            <span className="text-xs mt-1 inline-block">NSE is closed or data has not yet loaded.</span>
          </div>
        </div>
      )}
    </div>
  )
}
