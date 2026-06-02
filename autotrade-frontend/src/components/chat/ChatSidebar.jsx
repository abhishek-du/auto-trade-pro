import { useState, useEffect } from 'react'
import { TrendingUp, TrendingDown, Minus, Activity } from 'lucide-react'
import { apiFetch } from '../../api/client'

function MarketTicker({ symbol, label }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    const load = () =>
      apiFetch(`/api/v1/india/price/${encodeURIComponent(symbol)}`)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(() => {})
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [symbol])

  const chg = data?.change_pct ?? 0
  const isUp = chg > 0
  const isDown = chg < 0
  const ChgIcon = isUp ? TrendingUp : isDown ? TrendingDown : Minus
  const color = isUp ? '#10B981' : isDown ? '#EF4444' : '#64748B'

  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-xs text-muted">{label}</span>
      {data ? (
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-200 tabular-nums">
            {data.price?.toLocaleString('en-IN', { maximumFractionDigits: 0 }) ?? '—'}
          </span>
          <span className="flex items-center gap-0.5 text-[10px] font-semibold tabular-nums" style={{ color }}>
            <ChgIcon size={9} />
            {Math.abs(chg).toFixed(2)}%
          </span>
        </div>
      ) : (
        <span className="text-[10px] text-muted/50">Loading…</span>
      )}
    </div>
  )
}

function ActiveContextCard({ symbol, context }) {
  if (!context) return null
  const price   = context.price || {}
  const sig     = context.signal
  const ind     = context.indicators
  const chg     = price.change_pct ?? 0
  const color   = chg > 0 ? '#10B981' : chg < 0 ? '#EF4444' : '#64748B'
  const name    = context.display_name || symbol.replace('.NS', '')

  return (
    <div className="rounded-lg border border-border px-3 py-2 mb-2" style={{ background: '#080D1A' }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-bold text-slate-200 truncate">{name}</span>
        {price.price && (
          <span className="text-[10px] font-semibold tabular-nums" style={{ color }}>
            ₹{price.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {ind?.rsi != null && (
          <span className="text-[10px] text-muted">
            RSI <span className="text-slate-300 font-semibold">{ind.rsi.toFixed(0)}</span>
          </span>
        )}
        {sig && (
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
            sig.action.includes('BUY')  ? 'bg-profit/15 text-profit' :
            sig.action.includes('SELL') ? 'bg-loss/15 text-loss'     : 'bg-slate-700 text-slate-400'
          }`}>
            {sig.action}
          </span>
        )}
      </div>
    </div>
  )
}

const QUICK_QUESTIONS = [
  'What is the NIFTY 50 trend today?',
  'Which large caps are oversold?',
  'Show me IT sector stocks to watch',
  'Is the market bullish or bearish?',
  'Best dividend stocks on NSE',
  'BANKNIFTY support levels',
]

export default function ChatSidebar({ activeContexts, onSuggestionClick }) {
  const hasContexts = activeContexts && Object.keys(activeContexts).length > 0

  return (
    <aside className="w-64 shrink-0 flex flex-col gap-4 overflow-y-auto no-scrollbar py-4 px-3 border-l border-border"
      style={{ background: '#080D1A' }}>

      {/* Market Snapshot */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Activity size={12} className="text-cyan" />
          <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">Market Pulse</span>
        </div>
        <div className="rounded-xl border border-border px-3 py-1" style={{ background: '#0F1829' }}>
          <MarketTicker symbol="^NSEI"    label="NIFTY 50" />
          <MarketTicker symbol="^NSEBANK" label="BANKNIFTY" />
          <MarketTicker symbol="^CNXIT"   label="NIFTY IT" />
          <MarketTicker symbol="USDINR=X" label="USD/INR" />
        </div>
      </div>

      {/* Active Contexts */}
      {hasContexts && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-2">
            Stocks in Context
          </p>
          {Object.entries(activeContexts).map(([sym, ctx]) => (
            <ActiveContextCard key={sym} symbol={sym} context={ctx} />
          ))}
        </div>
      )}

      {/* Quick Questions */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted mb-2">
          Quick Questions
        </p>
        <div className="flex flex-col gap-1">
          {QUICK_QUESTIONS.map((q, i) => (
            <button
              key={i}
              onClick={() => onSuggestionClick?.(q)}
              className="text-left text-[11px] text-muted hover:text-slate-200 px-3 py-2 rounded-lg hover:bg-white/5 transition-colors border border-transparent hover:border-border"
            >
              {q}
            </button>
          ))}
        </div>
      </div>

      {/* Disclaimer */}
      <div className="mt-auto pt-2">
        <p className="text-[10px] text-muted/40 leading-relaxed text-center px-1">
          Avishk provides analysis based on live NSE data. Not financial advice. Always DYOR.
        </p>
      </div>
    </aside>
  )
}
