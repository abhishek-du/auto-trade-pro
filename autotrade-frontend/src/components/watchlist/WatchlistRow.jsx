import React, { useRef, useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { formatINR, formatVolume, formatPct } from '../../utils/indianFormat'

const SECTOR_COLORS = {
  IT:       'bg-blue-500/15 text-blue-400',
  Banking:  'bg-purple-500/15 text-purple-400',
  Pharma:   'bg-emerald-500/15 text-emerald-400',
  Auto:     'bg-orange-500/15 text-orange-400',
  FMCG:    'bg-teal-500/15 text-teal-400',
  Finance:  'bg-indigo-500/15 text-indigo-400',
  Energy:   'bg-amber-500/15 text-amber-400',
  Telecom:  'bg-cyan-500/15 text-cyan-400',
  Infra:    'bg-stone-500/15 text-stone-400',
  Cement:   'bg-gray-500/15 text-gray-400',
  Consumer: 'bg-pink-500/15 text-pink-400',
}

const SIGNAL_STYLES = {
  BUY:  { badge: 'bg-profit/15 text-profit border-profit/30', bar: 'bg-profit' },
  SELL: { badge: 'bg-loss/15 text-loss border-loss/30',       bar: 'bg-loss'   },
  HOLD: { badge: 'bg-warn/15 text-warn border-warn/30',       bar: 'bg-warn'   },
}

function VolRatioColor(ratio) {
  if (!ratio) return 'text-muted'
  if (ratio > 3.0) return 'text-loss font-bold'
  if (ratio > 2.0) return 'text-warn font-semibold'
  if (ratio < 0.5) return 'text-muted'
  return 'text-slate-300'
}

const WatchlistRow = React.memo(function WatchlistRow({ stock, onExpand, isExpanded }) {
  const [flashClass, setFlashClass] = useState('')
  const prevPriceRef = useRef(stock.price)

  useEffect(() => {
    if (stock.price === prevPriceRef.current) return
    const cls = stock.price > prevPriceRef.current ? 'flash-green' : 'flash-red'
    setFlashClass(cls)
    prevPriceRef.current = stock.price
    const t = setTimeout(() => setFlashClass(''), 800)
    return () => clearTimeout(t)
  }, [stock.price])

  const sym        = (stock.symbol || '').replace('.NS', '')
  const name       = (stock.name || sym).substring(0, 18)
  const isPositive = (stock.change || 0) >= 0
  const changeCls  = isPositive ? 'text-profit' : 'text-loss'

  // Day range bar — position of current price between today's low and high
  const high = stock.high || 0
  const low  = stock.low  || 0
  const rangeSpan = high - low
  const pricePos  = rangeSpan > 0
    ? Math.max(0, Math.min(100, ((stock.price - low) / rangeSpan) * 100))
    : 50

  // 52W position text
  const fh = stock.from_52w_high
  const fl = stock.from_52w_low
  const nearHigh = fh !== null && fh !== undefined && fh <= 2
  const nearLow  = fl !== null && fl !== undefined && fl <= 2

  const signalStyle = SIGNAL_STYLES[stock.signal] || null
  const confidence  = stock.signal_confidence || 0

  return (
    <tr
      onClick={onExpand}
      className={[
        'border-b border-border/50 cursor-pointer transition-colors group',
        isExpanded ? 'bg-accent/5' : 'hover:bg-white/[0.03]',
      ].join(' ')}
    >
      {/* Col 1 — Stock */}
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-2">
          <div>
            <div className="text-slate-200 text-[13px] font-bold leading-tight">{sym}</div>
            <div className="text-muted text-[10px] leading-tight mt-0.5">{name}</div>
          </div>
          <span className="text-[9px] font-bold text-muted/60 border border-border/50 px-1 py-0.5 rounded">NSE</span>
        </div>
      </td>

      {/* Col 2 — Sector */}
      <td className="px-3 py-2.5">
        {stock.sector
          ? <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${SECTOR_COLORS[stock.sector] || 'bg-slate-500/15 text-slate-400'}`}>
              {stock.sector}
            </span>
          : <span className="text-muted text-[10px]">—</span>
        }
      </td>

      {/* Col 3 — LTP */}
      <td className={`px-3 py-2.5 text-right ${flashClass}`}>
        <span className="text-slate-100 font-bold text-sm tabular-nums">
          {formatINR(stock.price)}
        </span>
      </td>

      {/* Col 4 — Change */}
      <td className="px-3 py-2.5 text-right">
        <span className={`text-xs tabular-nums font-medium ${changeCls}`}>
          {isPositive ? '▲' : '▼'} {formatINR(Math.abs(stock.change || 0))}
        </span>
      </td>

      {/* Col 5 — Change % */}
      <td className="px-3 py-2.5 text-right">
        <span className={[
          'text-[11px] font-semibold px-1.5 py-0.5 rounded tabular-nums',
          isPositive ? 'bg-profit/10 text-profit' : 'bg-loss/10 text-loss',
        ].join(' ')}>
          {formatPct(stock.change_pct)}
        </span>
      </td>

      {/* Col 6 — Volume */}
      <td className="px-3 py-2.5 text-right">
        <span className="text-muted text-xs tabular-nums">{formatVolume(stock.volume)}</span>
      </td>

      {/* Col 7 — Vol Ratio */}
      <td className="px-3 py-2.5 text-right">
        <span className={`text-xs tabular-nums ${VolRatioColor(stock.volume_ratio)}`} title="Volume vs 10-day average">
          {stock.volume_ratio != null ? stock.volume_ratio.toFixed(1) + 'x' : '—'}
        </span>
      </td>

      {/* Col 8 — Day H/L */}
      <td className="px-3 py-2.5 text-right">
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-[10px] text-profit tabular-nums">H: {formatINR(high)}</span>
          <span className="text-[10px] text-loss  tabular-nums">L: {formatINR(low)}</span>
          {/* Mini range bar */}
          <div className="w-12 h-1 bg-border/60 rounded-full overflow-hidden relative mt-0.5">
            <div
              className="absolute top-0 bottom-0 left-0 bg-profit/70 rounded-full"
              style={{ width: `${pricePos}%` }}
            />
          </div>
        </div>
      </td>

      {/* Col 9 — 52W Position */}
      <td className="px-3 py-2.5 text-right">
        {nearHigh
          ? <span className="text-[10px] font-bold text-profit">★ Near 52W High</span>
          : nearLow
          ? <span className="text-[10px] font-bold text-loss">↓ Near 52W Low</span>
          : (
            <div className="flex flex-col items-end gap-0.5">
              {fh != null && <span className="text-[10px] text-muted tabular-nums">{fh.toFixed(1)}% below H</span>}
              {fl != null && <span className="text-[10px] text-muted tabular-nums">{fl.toFixed(1)}% above L</span>}
              {fh == null && fl == null && <span className="text-muted text-xs">—</span>}
            </div>
          )
        }
      </td>

      {/* Col 10 — Signal */}
      <td className="px-3 py-2.5 text-center">
        {signalStyle
          ? (
            <div className="flex flex-col items-center gap-1">
              <span className={`text-[10px] font-bold border px-1.5 py-0.5 rounded ${signalStyle.badge}`}>
                {stock.signal}
              </span>
              {/* Confidence bar */}
              <div className="w-10 h-1 bg-border/60 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${signalStyle.bar}`} style={{ width: `${confidence}%` }} />
              </div>
              <span className="text-[9px] text-muted tabular-nums">{Math.round(confidence)}%</span>
            </div>
          )
          : <span className="text-muted text-sm">—</span>
        }
      </td>
    </tr>
  )
}, (prev, next) =>
  prev.stock.price      === next.stock.price      &&
  prev.stock.change_pct === next.stock.change_pct &&
  prev.stock.volume     === next.stock.volume      &&
  prev.stock.signal     === next.stock.signal      &&
  prev.isExpanded       === next.isExpanded
)

export default WatchlistRow
