import { useState, useRef, useEffect } from 'react'
import { TrendingUp, TrendingDown, Minus, ChevronUp, ChevronDown, Trash2, ShoppingCart } from 'lucide-react'
import { formatINR, formatPct } from '../../utils/indianFormat'

function PnlBadge({ val, pct }) {
  const pos = val >= 0
  return (
    <div className={`flex flex-col items-end ${pos ? 'text-profit' : 'text-loss'}`}>
      <span className="text-xs font-bold tabular-nums">{formatINR(val)}</span>
      <span className="text-[10px] tabular-nums">{formatPct(pct)}</span>
    </div>
  )
}

function WeightBar({ weight }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-white/10 rounded-full overflow-hidden">
        <div
          className="h-full bg-cyan/50 rounded-full"
          style={{ width: `${Math.min(weight, 100)}%` }}
        />
      </div>
      <span className="text-[10px] text-muted tabular-nums">{weight?.toFixed(1)}%</span>
    </div>
  )
}

function FlashCell({ value, children }) {
  const [flash, setFlash] = useState(false)
  const prev = useRef(value)
  useEffect(() => {
    if (prev.current !== value && prev.current !== undefined) {
      setFlash(true)
      const t = setTimeout(() => setFlash(false), 600)
      prev.current = value
      return () => clearTimeout(t)
    }
    prev.current = value
  }, [value])
  return (
    <span className={`transition-colors duration-300 ${flash ? 'text-cyan' : ''}`}>
      {children}
    </span>
  )
}

const COLS = [
  { key: 'symbol',        label: 'Holding'       },
  { key: 'current_price', label: 'LTP/NAV'       },
  { key: 'quantity',      label: 'Qty/Units'     },
  { key: 'avg_buy_price', label: 'Avg Cost'      },
  { key: 'invested',      label: 'Invested'      },
  { key: 'current_value', label: 'Market Value'  },
  { key: 'pnl',           label: 'P&L'           },
  { key: 'day_pnl',       label: "Today"         },
  { key: 'weight',        label: 'Weight'        },
  { key: 'xirr',          label: 'XIRR'          },
]

export default function HoldingsTable({ holdings, onSell, onDelete }) {
  const [sort, setSort] = useState({ key: 'current_value', dir: 'desc' })

  function toggleSort(key) {
    setSort(s => s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' })
  }

  const sorted = [...(holdings || [])].sort((a, b) => {
    const av = a[sort.key] ?? 0
    const bv = b[sort.key] ?? 0
    return sort.dir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1)
  })

  if (!sorted.length) {
    return (
      <div className="text-center py-12 text-muted text-sm">
        No holdings yet. Add your first stock or mutual fund using the button above.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {COLS.map(col => (
              <th
                key={col.key}
                onClick={() => toggleSort(col.key)}
                className="text-left px-3 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider cursor-pointer select-none hover:text-slate-300 transition-colors whitespace-nowrap"
              >
                <span className="flex items-center gap-0.5">
                  {col.label}
                  {sort.key === col.key
                    ? sort.dir === 'asc' ? <ChevronUp size={10} /> : <ChevronDown size={10} />
                    : null}
                </span>
              </th>
            ))}
            <th className="px-3 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {sorted.map(h => {
            const dayPos = (h.day_pnl ?? 0) >= 0
            const pnlPos = (h.pnl ?? 0) >= 0
            return (
              <tr key={h.id} className="hover:bg-white/[0.02] transition-colors">
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <p className="text-slate-200 font-semibold">
                      {h.display_symbol || (h.symbol?.startsWith('MF:') ? h.symbol.slice(3) : h.symbol?.replace('.NS', ''))}
                    </p>
                    {h.source === 'ZERODHA' && (
                      <span className="text-[8px] font-bold uppercase px-1 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/30 shrink-0" title="Synced from Zerodha Demat">Z</span>
                    )}
                    {h.source === 'MUTUAL_FUND' && (
                      <span className="text-[8px] font-bold uppercase px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-400 shrink-0">MF</span>
                    )}
                    {h.source === 'MANUAL' && !h.is_mf && (
                      <span className="text-[8px] font-bold uppercase px-1 py-0.5 rounded bg-slate-500/15 text-muted border border-border shrink-0" title="Manually added">M</span>
                    )}
                  </div>
                  <p className="text-muted text-[10px] max-w-[140px] truncate">{h.company_name}</p>
                  {h.sector && (
                    <span className="text-[9px] bg-white/5 text-muted px-1.5 py-0.5 rounded mt-0.5 inline-block">{h.sector}</span>
                  )}
                </td>
                <td className="px-3 py-3">
                  <FlashCell value={h.current_price}>
                    <span className="text-slate-100 font-mono tabular-nums text-sm">
                      {h.current_price ? formatINR(h.current_price) : '—'}
                    </span>
                  </FlashCell>
                  {h.day_change_pct != null && (
                    <p className={`text-[10px] tabular-nums ${dayPos ? 'text-profit' : 'text-loss'}`}>
                      {dayPos ? '+' : ''}{h.day_change_pct?.toFixed(2)}%
                    </p>
                  )}
                </td>
                <td className="px-3 py-3 text-slate-300 tabular-nums">{h.quantity}</td>
                <td className="px-3 py-3 text-slate-300 tabular-nums font-mono">{formatINR(h.avg_buy_price)}</td>
                <td className="px-3 py-3 text-slate-300 tabular-nums">{formatINR(h.invested)}</td>
                <td className="px-3 py-3">
                  <FlashCell value={h.current_value}>
                    <span className="text-slate-100 font-semibold tabular-nums">{formatINR(h.current_value)}</span>
                  </FlashCell>
                </td>
                <td className="px-3 py-3">
                  <PnlBadge val={h.pnl ?? 0} pct={h.pnl_pct ?? 0} />
                </td>
                <td className="px-3 py-3">
                  <span className={`text-xs tabular-nums font-semibold ${dayPos ? 'text-profit' : 'text-loss'}`}>
                    {formatINR(h.day_pnl ?? 0)}
                  </span>
                </td>
                <td className="px-3 py-3">
                  <WeightBar weight={h.weight ?? 0} />
                </td>
                <td className="px-3 py-3">
                  {h.xirr != null ? (
                    <span className={`text-xs font-bold tabular-nums ${h.xirr >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {formatPct(h.xirr)}
                    </span>
                  ) : <span className="text-muted text-xs">—</span>}
                </td>
                <td className="px-3 py-3">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => onSell(h)}
                      title="Sell"
                      className="p-1.5 rounded text-muted hover:text-profit hover:bg-profit/10 transition-colors"
                    >
                      <ShoppingCart size={13} />
                    </button>
                    <button
                      onClick={() => onDelete(h)}
                      title="Remove holding"
                      className="p-1.5 rounded text-muted hover:text-loss hover:bg-loss/10 transition-colors"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
