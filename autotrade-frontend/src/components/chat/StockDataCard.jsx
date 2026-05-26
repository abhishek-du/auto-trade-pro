import { ExternalLink, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { formatINR, formatPct, timeSince } from '../../utils/indianFormat'

function MetricPill({ label, value, color }) {
  return (
    <div className="flex flex-col items-center px-2.5 py-1.5 rounded-lg border border-border shrink-0"
      style={{ background: '#080D1A', minWidth: 64 }}>
      <span className="text-[9px] text-muted uppercase tracking-wide">{label}</span>
      <span className="text-xs font-bold mt-0.5" style={{ color: color || '#94A3B8' }}>{value}</span>
    </div>
  )
}

function rsiColor(rsi) {
  if (rsi == null) return '#94A3B8'
  if (rsi <= 30) return '#10B981'
  if (rsi >= 70) return '#EF4444'
  return '#94A3B8'
}

function signalColor(action) {
  if (!action) return '#94A3B8'
  const a = action.toUpperCase()
  if (a.includes('BUY') || a.includes('LONG'))  return '#10B981'
  if (a.includes('SELL') || a.includes('SHORT')) return '#EF4444'
  return '#94A3B8'
}

export default function StockDataCard({ symbol, context }) {
  if (!context) return null

  const price   = context.price     || {}
  const ind     = context.indicators || null
  const sig     = context.signal     || null
  const pat     = context.patterns   || null
  const news    = context.sentiment?.news || []
  const name    = context.display_name || symbol.replace('.NS', '')
  const builtAt = context.context_built_at

  const chg    = price.change     ?? 0
  const chgPct = price.change_pct ?? 0
  const isUp   = chg >= 0
  const ChgIcon = chg > 0 ? TrendingUp : chg < 0 ? TrendingDown : Minus
  const chgColor = chg > 0 ? '#10B981' : chg < 0 ? '#EF4444' : '#94A3B8'

  return (
    <div className="mt-2 rounded-xl border border-border overflow-hidden"
      style={{ background: 'linear-gradient(135deg,#0A1120,#0D1527)' }}>

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border/50">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-bold text-slate-200 truncate">{name}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted">{symbol}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {price.price ? (
            <>
              <span className="text-sm font-bold text-slate-100 tabular-nums">{formatINR(price.price)}</span>
              <span className="flex items-center gap-0.5 text-xs font-semibold tabular-nums" style={{ color: chgColor }}>
                <ChgIcon size={11} />
                {formatPct(chgPct)}
              </span>
            </>
          ) : (
            <span className="text-xs text-muted">No price data</span>
          )}
        </div>
      </div>

      {/* Metrics strip */}
      <div className="flex gap-2 px-3 py-2 overflow-x-auto no-scrollbar">
        {ind?.rsi != null && (
          <MetricPill label="RSI" value={ind.rsi.toFixed(1)} color={rsiColor(ind.rsi)} />
        )}
        {ind?.macd_cross && ind.macd_cross !== 'NONE' && (
          <MetricPill label="MACD"
            value={ind.macd_cross === 'BULLISH_CROSS' ? '▲ Bull' : '▼ Bear'}
            color={ind.macd_cross === 'BULLISH_CROSS' ? '#10B981' : '#EF4444'} />
        )}
        {ind?.ema_trend && (
          <MetricPill label="Trend"
            value={ind.ema_trend.replace('STRONG_', '').replace('_', ' ')}
            color={ind.ema_trend.includes('BULL') ? '#10B981' : ind.ema_trend.includes('BEAR') ? '#EF4444' : '#94A3B8'} />
        )}
        {pat?.strongest && (
          <MetricPill label="Pattern"
            value={pat.strongest.replace(/_/g, ' ').slice(0, 10)}
            color={pat.direction === 'BULLISH' ? '#10B981' : pat.direction === 'BEARISH' ? '#EF4444' : '#94A3B8'} />
        )}
        {sig && (
          <MetricPill label="Signal"
            value={`${sig.action} ${sig.confidence.toFixed(0)}%`}
            color={signalColor(sig.action)} />
        )}
        {ind?.composite_score != null && (
          <MetricPill label="Score"
            value={ind.composite_score.toFixed(0)}
            color={ind.composite_score > 20 ? '#10B981' : ind.composite_score < -20 ? '#EF4444' : '#94A3B8'} />
        )}
      </div>

      {/* News ticker */}
      {news.length > 0 && (
        <div className="px-3 pb-2 flex gap-2 overflow-x-auto no-scrollbar">
          {news.slice(0, 2).map((n, i) => (
            <div key={i} className="flex items-start gap-1.5 shrink-0 max-w-[260px]">
              <span className="text-[10px] mt-0.5">
                {n.sentiment === 'positive' ? '🟢' : n.sentiment === 'negative' ? '🔴' : '⚪'}
              </span>
              <span className="text-[10px] text-muted leading-tight line-clamp-2">{n.headline}</span>
            </div>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between px-3 py-1.5 border-t border-border/40"
        style={{ background: '#080D1A' }}>
        <span className="text-[10px] text-muted/60">
          {builtAt ? `Updated ${timeSince(builtAt)}` : 'Live data'}
        </span>
        <a href={`/chart?symbol=${symbol.replace('.NS', '')}`}
          className="flex items-center gap-1 text-[10px] text-accent hover:text-cyan transition-colors">
          View Chart <ExternalLink size={9} />
        </a>
      </div>
    </div>
  )
}
