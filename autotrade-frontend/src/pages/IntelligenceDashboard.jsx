import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BrainCircuit, RefreshCw, TrendingUp, TrendingDown, Minus,
  Activity, Wallet, ChevronRight,
} from 'lucide-react'
import { useIntelligenceHub } from '../hooks/useIntelligenceHub'
import { formatINR } from '../utils/indianFormat'

const SIGNAL_STYLE = {
  STRONG_BUY:  'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
  BUY:         'bg-emerald-500/12 text-emerald-300 border-emerald-500/25',
  NEUTRAL:     'bg-slate-500/15 text-slate-300 border-slate-500/30',
  SELL:        'bg-red-500/12 text-red-300 border-red-500/25',
  STRONG_SELL: 'bg-red-500/20 text-red-400 border-red-500/40',
}

const MOOD_COLOR = {
  STRONGLY_BULLISH: 'text-emerald-400', BULLISH: 'text-emerald-300',
  NEUTRAL: 'text-slate-300', BEARISH: 'text-red-300', STRONGLY_BEARISH: 'text-red-400',
}

function timeAgo(iso) {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso + 'Z').getTime()) / 1000
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

function MacroStrip({ macro }) {
  if (!macro) return null
  const bias = macro.total_macro_bias ?? 0
  const biasColor = bias > 0 ? 'text-emerald-400' : bias < 0 ? 'text-red-400' : 'text-slate-300'
  const fii = macro.fii_net_3d ?? 0
  const cards = [
    { label: 'FII 3-Day Flow', value: `${fii >= 0 ? '+' : ''}₹${Math.abs(fii).toFixed(0)} Cr`, color: fii >= 0 ? 'text-emerald-400' : 'text-red-400', Icon: fii >= 0 ? TrendingUp : TrendingDown },
    { label: 'DII 3-Day Flow', value: `₹${(macro.dii_net_3d ?? 0).toFixed(0)} Cr`, color: 'text-slate-200', Icon: Activity },
    { label: 'India VIX', value: `${macro.india_vix ?? '—'} ${macro.vix_label || ''}`, color: 'text-amber-300', Icon: Activity },
    { label: 'Market Mood', value: macro.nse_market_mood || 'NEUTRAL', color: MOOD_COLOR[macro.nse_market_mood] || 'text-slate-300', Icon: Activity },
    { label: 'Macro Bias', value: `${bias > 0 ? '+' : ''}${bias}`, color: biasColor, Icon: bias >= 0 ? TrendingUp : TrendingDown },
  ]
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {cards.map(c => (
        <div key={c.label} className="rounded-xl border border-border p-3 space-y-1" style={{ background: '#0F1829' }}>
          <div className="flex items-center gap-1.5">
            <c.Icon size={12} className={c.color} />
            <p className="text-muted text-[10px] uppercase tracking-widest">{c.label}</p>
          </div>
          <p className={`font-bold text-base tabular-nums ${c.color}`}>{c.value}</p>
        </div>
      ))}
    </div>
  )
}

function SectorRotation({ sectors }) {
  if (!sectors?.sector_biases) return null
  const entries = Object.entries(sectors.sector_biases).sort((a, b) => b[1] - a[1])
  const barColor = (v) => v >= 1 ? 'bg-emerald-500' : v <= -1 ? 'bg-red-500' : 'bg-slate-600'
  return (
    <div className="rounded-xl border border-border p-4 space-y-3" style={{ background: '#0F1829' }}>
      <h3 className="text-slate-200 font-semibold text-sm">Sector Rotation</h3>
      <div className="flex flex-wrap gap-2">
        {entries.map(([sec, bias]) => (
          <div key={sec} className="flex items-center gap-1.5 px-2 py-1 rounded-lg border border-border bg-white/[0.02]">
            <span className={`w-2 h-2 rounded-full ${barColor(bias)}`} />
            <span className="text-xs text-slate-300">{sec}</span>
            <span className="text-[10px] text-muted tabular-nums">{bias > 0 ? '+' : ''}{bias}</span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
        <span className="text-emerald-400">Into: {sectors.rotating_into?.join(', ') || '—'}</span>
        <span className="text-red-400">Out of: {sectors.rotating_out_of?.join(', ') || '—'}</span>
      </div>
    </div>
  )
}

function ScoresTable({ scores, onSelect }) {
  const [filter, setFilter] = useState('All')
  const FILTERS = ['All', 'STRONG_BUY', 'BUY', 'NEUTRAL', 'SELL', 'Blocked']
  const filtered = scores.filter(s => {
    if (filter === 'All') return true
    if (filter === 'Blocked') return s.is_blocked
    return s.signal === filter
  })
  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap">
        <h3 className="text-slate-200 font-semibold text-sm mr-2">Universe Scores</h3>
        {FILTERS.map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`text-[11px] px-2 py-0.5 rounded-md border ${filter === f ? 'bg-accent/20 text-cyan border-accent/40' : 'text-muted border-border hover:text-slate-300'}`}>
            {f.replace('_', ' ')}
          </button>
        ))}
        <span className="ml-auto text-muted text-[11px]">{filtered.length} shown</span>
      </div>
      <div className="overflow-x-auto max-h-[460px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0" style={{ background: '#0F1829' }}>
            <tr className="border-b border-border text-muted">
              {['#', 'Symbol', 'Score', 'Signal', 'Tech', 'News', 'Sector', 'Macro', 'Earn', 'Fund', ''].map(h => (
                <th key={h} className="px-2.5 py-2 text-left font-semibold uppercase tracking-wide whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {filtered.map(s => {
              const r = s.reasoning || {}
              return (
                <tr key={s.symbol} onClick={() => onSelect(s)}
                  className={`hover:bg-white/[0.03] cursor-pointer ${s.is_blocked ? 'opacity-50' : ''}`}>
                  <td className="px-2.5 py-2 text-muted">{s.rank}</td>
                  <td className="px-2.5 py-2 font-bold text-slate-100">{s.symbol.replace('.NS', '')}</td>
                  <td className="px-2.5 py-2 font-bold tabular-nums text-slate-100">{s.master_score?.toFixed(1)}</td>
                  <td className="px-2.5 py-2">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${SIGNAL_STYLE[s.signal] || SIGNAL_STYLE.NEUTRAL}`}>
                      {s.signal.replace('_', ' ')}
                    </span>
                  </td>
                  {['technical', 'news', 'sector', 'macro', 'earnings', 'fundamental'].map(k => (
                    <td key={k} className={`px-2.5 py-2 tabular-nums ${(r[k] || 0) > 0 ? 'text-emerald-400' : (r[k] || 0) < 0 ? 'text-red-400' : 'text-muted'}`}>
                      {(r[k] || 0).toFixed(0)}
                    </td>
                  ))}
                  <td className="px-2.5 py-2 text-muted">
                    {s.is_blocked ? <span className="text-amber-400 text-[10px]" title={s.blocked_reason}>⚠</span> : <ChevronRight size={12} />}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function BreakdownPanel({ stock, onClose }) {
  if (!stock) return null
  const r = stock.reasoning || {}
  const rows = [
    ['Technical', r.technical, 35], ['News', r.news, 15], ['Sector', r.sector, 15],
    ['Macro', r.macro, 10], ['Earnings', r.earnings, 10], ['Fundamental', r.fundamental, 10], ['Options', r.options, 5],
  ]
  return (
    <div className="rounded-xl border border-accent/30 p-4 space-y-3" style={{ background: '#0F1829' }}>
      <div className="flex items-center justify-between">
        <h3 className="text-slate-100 font-bold text-sm">{stock.symbol.replace('.NS', '')} breakdown</h3>
        <button onClick={onClose} className="text-muted text-xs hover:text-white">✕</button>
      </div>
      <div className="flex items-center gap-3">
        <span className={`text-2xl font-bold tabular-nums ${stock.master_score >= 25 ? 'text-emerald-400' : stock.master_score <= -25 ? 'text-red-400' : 'text-slate-200'}`}>
          {stock.master_score?.toFixed(1)}
        </span>
        <span className={`text-xs font-bold px-2 py-0.5 rounded border ${SIGNAL_STYLE[stock.signal]}`}>{stock.signal.replace('_', ' ')}</span>
        <span className="text-muted text-xs">{stock.regime}</span>
      </div>
      <div className="space-y-1.5">
        {rows.map(([label, val, weight]) => (
          <div key={label} className="flex items-center gap-2">
            <span className="text-muted text-[11px] w-24">{label} <span className="opacity-50">({weight}%)</span></span>
            <div className="flex-1 h-2 rounded-full bg-border overflow-hidden relative">
              <div className={`h-full ${(val || 0) >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`}
                style={{ width: `${Math.min(Math.abs(val || 0), 100)}%`, marginLeft: (val || 0) < 0 ? 'auto' : 0 }} />
            </div>
            <span className={`text-[11px] tabular-nums w-10 text-right ${(val || 0) > 0 ? 'text-emerald-400' : (val || 0) < 0 ? 'text-red-400' : 'text-muted'}`}>
              {(val || 0).toFixed(0)}
            </span>
          </div>
        ))}
      </div>
      {stock.is_blocked && (
        <p className="text-amber-400 text-[11px]">⚠ Blocked: {stock.blocked_reason}</p>
      )}
    </div>
  )
}

function MFSignals({ mfSignals }) {
  if (!mfSignals?.length) return null
  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <Wallet size={14} className="text-emerald-400" />
        <h3 className="text-slate-200 font-semibold text-sm">Mutual Fund Signals</h3>
      </div>
      <table className="w-full text-xs">
        <tbody className="divide-y divide-border/40">
          {mfSignals.map(m => (
            <tr key={m.scheme_code} className="hover:bg-white/[0.02]">
              <td className="px-4 py-2 text-slate-200">{m.scheme_name}</td>
              <td className="px-4 py-2 text-muted">{m.category}</td>
              <td className="px-4 py-2">
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${m.signal === 'ADD' ? 'bg-emerald-500/15 text-emerald-400' : m.signal === 'REDUCE' ? 'bg-red-500/15 text-red-400' : 'bg-slate-500/15 text-slate-300'}`}>{m.signal}</span>
              </td>
              <td className="px-4 py-2 tabular-nums text-slate-200">{m.master_score?.toFixed(0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function IntelligenceDashboard() {
  const { context, scores, mfSignals, cycleLog, loading, lastCycleAt, triggering, triggerCycle } = useIntelligenceHub()
  const [selected, setSelected] = useState(null)

  return (
    <div className="space-y-5 fade-in">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl" style={{ background: 'rgba(139,92,246,0.12)' }}>
            <BrainCircuit size={20} className="text-violet-400" />
          </div>
          <div>
            <h1 className="text-slate-100 font-bold text-xl">Master Intelligence Hub</h1>
            <p className="text-muted text-sm">All data sources unified into one decision engine · last cycle {timeAgo(lastCycleAt)}</p>
          </div>
        </div>
        <button onClick={triggerCycle} disabled={triggering}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gradient-to-r from-violet-600 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50">
          <RefreshCw size={14} className={triggering ? 'animate-spin' : ''} /> {triggering ? 'Running…' : 'Trigger Cycle'}
        </button>
      </div>

      {loading ? (
        <div className="text-center py-16 text-muted">Loading intelligence…</div>
      ) : (
        <>
          <MacroStrip macro={context?.macro} />
          <SectorRotation sectors={context?.sectors} />

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2">
              <ScoresTable scores={scores} onSelect={setSelected} />
            </div>
            <div className="space-y-4">
              {selected
                ? <BreakdownPanel stock={selected} onClose={() => setSelected(null)} />
                : <div className="rounded-xl border border-border p-6 text-center text-muted text-sm" style={{ background: '#0F1829' }}>Click a stock to see its score breakdown</div>}
              <MFSignals mfSignals={mfSignals} />
            </div>
          </div>

          {/* Cycle history */}
          {cycleLog.length > 0 && (
            <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
              <div className="px-4 py-3 border-b border-border">
                <h3 className="text-slate-200 font-semibold text-sm">Recent Cycles</h3>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted">
                    {['Time', 'Scored', 'Decisions', 'Macro', 'Duration', 'Status'].map(h => (
                      <th key={h} className="px-4 py-2 text-left font-semibold uppercase tracking-wide">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {cycleLog.map(c => (
                    <tr key={c.id} className="hover:bg-white/[0.02]">
                      <td className="px-4 py-2 text-muted">{timeAgo(c.cycle_end || c.cycle_start)}</td>
                      <td className="px-4 py-2 text-slate-200">{c.symbols_scored}</td>
                      <td className="px-4 py-2 text-slate-200">{c.decisions_made}</td>
                      <td className="px-4 py-2 tabular-nums">{(c.macro_context?.total_macro_bias ?? 0) > 0 ? '+' : ''}{c.macro_context?.total_macro_bias ?? 0}</td>
                      <td className="px-4 py-2 text-muted">{c.duration_seconds?.toFixed(1)}s</td>
                      <td className="px-4 py-2">
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${c.status === 'complete' ? 'bg-emerald-500/15 text-emerald-400' : c.status === 'error' ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'}`}>{c.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
