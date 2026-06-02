import { useState, useEffect } from 'react'
import { X, Loader2, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { formatINR, timeSince } from '../../utils/indianFormat'
import toast from 'react-hot-toast'
import { apiFetch } from '../../api/client'

const INDICATOR_LABELS = {
  rsi_signal:   { label: 'RSI',        valKey: 'rsi'      },
  macd_signal:  { label: 'MACD',       valKey: 'macd_signal' },
  supertrend:   { label: 'Supertrend', valKey: 'supertrend'  },
  vwap_position:{ label: 'VWAP',       valKey: 'vwap_position' },
  ema_trend:    { label: 'EMA Trend',  valKey: 'ema_trend'  },
}

function indicatorBadge(val) {
  if (!val) return 'bg-muted/15 text-muted'
  const v = String(val).toUpperCase()
  if (v.includes('BULL') || v.includes('BUY') || v.includes('ABOVE') || v === 'OVERSOLD')
    return 'bg-profit/15 text-profit'
  if (v.includes('BEAR') || v.includes('SELL') || v.includes('BELOW') || v === 'OVERBOUGHT')
    return 'bg-loss/15 text-loss'
  return 'bg-warn/15 text-warn'
}

function indicatorLabel(val) {
  if (!val) return '—'
  const v = String(val).replace(/_/g, ' ')
  if (v.includes('BULL') || v.includes('BUY') || v.includes('ABOVE') || v === 'OVERSOLD') return 'BULLISH'
  if (v.includes('BEAR') || v.includes('SELL') || v.includes('BELOW') || v === 'OVERBOUGHT') return 'BEARISH'
  return 'NEUTRAL'
}

function OverallBadge({ overall }) {
  const cls = overall === 'BULLISH'
    ? 'bg-profit/20 text-profit border-profit/30'
    : overall === 'BEARISH'
    ? 'bg-loss/20 text-loss border-loss/30'
    : 'bg-warn/20 text-warn border-warn/30'
  const Icon = overall === 'BULLISH' ? TrendingUp : overall === 'BEARISH' ? TrendingDown : Minus
  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border ${cls} mt-3`}>
      <Icon size={14} />
      <span className="font-bold text-sm">{overall}</span>
    </div>
  )
}

function SentimentBadge({ sentiment }) {
  if (!sentiment) return null
  const s = sentiment.toLowerCase()
  const cls = s === 'positive' ? 'bg-profit/15 text-profit'
    : s === 'negative' ? 'bg-loss/15 text-loss'
    : 'bg-muted/15 text-muted'
  const label = s === 'positive' ? 'Bullish' : s === 'negative' ? 'Bearish' : 'Neutral'
  return <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${cls}`}>{label}</span>
}

export default function WatchlistDetailPanel({ stock, onClose }) {
  const [detail, setDetail]     = useState(null)
  const [loading, setLoading]   = useState(true)
  const [generating, setGen]    = useState(false)

  const sym = (stock.symbol || '').replace('.NS', '')

  useEffect(() => {
    setLoading(true)
    apiFetch(`/api/v1/india/watchlist/${sym}`)
      .then(d => { setDetail(d); setLoading(false) })
      .catch(() => { setLoading(false) })
  }, [sym])

  async function handleGenerateSignal() {
    setGen(true)
    try {
      await apiFetch('/api/v1/india/signals/trigger', { method: 'POST' })
      toast.success('Signal generation triggered')
    } catch {
      toast.error('Failed to trigger signals')
    } finally {
      setGen(false)
    }
  }

  const tech    = detail?.technical_summary || {}
  const aiData  = detail?.ai_analysis || {}
  const reasons = Array.isArray(aiData?.reasoning_points) ? aiData.reasoning_points : []

  return (
    <div className="bg-surface border-t border-border animate-[fade-in_0.2s_ease-out] relative">
      {/* Close */}
      <button
        onClick={onClose}
        className="absolute top-3 right-3 text-muted hover:text-slate-200 transition-colors"
      >
        <X size={14} />
      </button>

      {loading ? (
        <div className="flex items-center justify-center py-10 gap-2 text-muted text-sm">
          <Loader2 size={16} className="animate-spin" />
          Loading analysis…
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-0 divide-y lg:divide-y-0 lg:divide-x divide-border">

          {/* ── Left: Technical snapshot ─────────────────────────────────── */}
          <div className="p-4">
            <p className="text-slate-200 text-xs font-semibold uppercase tracking-wider mb-3">Technical Analysis</p>
            <div className="space-y-2">
              {Object.entries(INDICATOR_LABELS).map(([key, { label, valKey }]) => {
                const rawVal = tech[valKey]
                const dispVal = key === 'rsi_signal' && tech.rsi != null
                  ? `${tech.rsi} (${tech.rsi_signal})`
                  : rawVal
                return (
                  <div key={key} className="flex items-center justify-between">
                    <span className="text-muted text-[11px]">{label}</span>
                    <div className="flex items-center gap-1.5">
                      <span className="text-slate-300 text-[11px] tabular-nums">{dispVal ?? '—'}</span>
                      <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${indicatorBadge(rawVal)}`}>
                        {indicatorLabel(rawVal)}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
            <OverallBadge overall={tech.overall || 'NEUTRAL'} />
          </div>

          {/* ── Center: AI Signal reasoning ──────────────────────────────── */}
          <div className="p-4">
            <div className="flex items-center justify-between mb-3">
              <p className="text-slate-200 text-xs font-semibold uppercase tracking-wider">AI Analysis</p>
              {detail?.signal_confidence && (
                <span className="text-[10px] text-muted">{Math.round(detail.signal_confidence)}% confidence</span>
              )}
            </div>

            {reasons.length > 0 ? (
              <ul className="space-y-2">
                {reasons.slice(0, 5).map((point, i) => (
                  <li key={i} className="flex items-start gap-2 text-[11px] text-slate-300 leading-snug">
                    <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${
                      point.toLowerCase().includes('bull') || point.toLowerCase().includes('buy') ? 'bg-profit' :
                      point.toLowerCase().includes('bear') || point.toLowerCase().includes('sell') ? 'bg-loss' :
                      'bg-warn'
                    }`} />
                    {point}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted text-[11px] mb-3">No signal generated yet for {sym}.</p>
            )}

            <button
              onClick={handleGenerateSignal}
              disabled={generating}
              className="mt-3 w-full py-1.5 rounded-lg border border-accent/30 text-accent text-[11px] font-medium hover:bg-accent/10 disabled:opacity-50 transition-all"
            >
              {generating ? 'Generating…' : 'Generate Signal'}
            </button>
          </div>

          {/* ── Right: Stock info + news ──────────────────────────────────── */}
          <div className="p-4">
            <p className="text-slate-200 text-xs font-semibold uppercase tracking-wider mb-3">Stock Info</p>
            <div className="space-y-1.5 text-[11px]">
              {[
                ['Market Cap', stock.market_cap != null ? `₹${stock.market_cap.toFixed(0)} Cr` : '—'],
                ['P/E Ratio',  stock.pe_ratio   != null ? stock.pe_ratio.toFixed(1)  : '—'],
                ['P/B Ratio',  stock.pb_ratio   != null ? stock.pb_ratio.toFixed(2)  : '—'],
                ['Div Yield',  stock.dividend_yield != null ? `${stock.dividend_yield.toFixed(2)}%` : '—'],
                ['Beta',       stock.beta != null ? stock.beta.toFixed(2) : '—'],
                ['Sector',     stock.sector || '—'],
              ].map(([key, val]) => (
                <div key={key} className="flex justify-between items-center">
                  <span className="text-muted">{key}</span>
                  <span className="text-slate-300 font-medium tabular-nums">{val}</span>
                </div>
              ))}
            </div>

            {(detail?.recent_news || []).length > 0 && (
              <>
                <div className="border-t border-border my-3" />
                <p className="text-slate-200 text-[10px] font-semibold uppercase tracking-wider mb-2">Recent News</p>
                <div className="space-y-2">
                  {(detail.recent_news || []).map((n, i) => (
                    <div key={i} className="space-y-0.5">
                      <p className="text-slate-300 text-[10px] leading-snug line-clamp-2">{n.headline}</p>
                      <div className="flex items-center gap-1.5">
                        <span className="text-muted text-[9px]">{n.source}</span>
                        {n.published_at && <span className="text-muted text-[9px]">· {timeSince(n.published_at)}</span>}
                        <SentimentBadge sentiment={n.sentiment} />
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

        </div>
      )}
    </div>
  )
}
