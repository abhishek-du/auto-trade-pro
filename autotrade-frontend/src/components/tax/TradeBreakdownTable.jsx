import { useState, useMemo } from 'react'
import { Download, ChevronUp, ChevronDown } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

function TypeBadge({ trade }) {
  const { gain_type, gross_gain, is_slab_taxed, tax_rate } = trade
  if (gross_gain < 0) {
    const type = gain_type === 'LTCG' ? 'LTCL' : 'STCL'
    return (
      <span className="px-2 py-0.5 rounded-full border text-[10px] font-bold border-red-500/40 text-red-400">
        {type}
      </span>
    )
  }
  if (gain_type === 'LTCG') {
    return (
      <span className="px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-400 text-[10px] font-bold">
        LTCG 12.5%
      </span>
    )
  }
  if (gain_type === 'DEBT_SLAB') {
    return (
      <span className="px-2 py-0.5 rounded-full bg-purple-500/15 text-purple-400 text-[10px] font-bold">
        Debt (Slab)
      </span>
    )
  }
  return (
    <span className="px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 text-[10px] font-bold">
      STCG {(tax_rate * 100).toFixed(0)}%
    </span>
  )
}

function fmtDate(d) {
  if (!d) return '—'
  const dt = new Date(d)
  return dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

function fmtHolding(days) {
  if (days < 30)  return `${days}d`
  if (days < 365) return `${Math.round(days / 30)}m`
  return `${(days / 365).toFixed(1)}y`
}

const FILTERS = ['All', 'STCG', 'LTCG', 'Losses', 'Debt']

function exportCSV(trades) {
  const headers = ['Symbol','Company','Buy Date','Sell Date','Qty','Buy Price','Sell Price','Holding Days','Gain/Loss','Type']
  const rows = trades.map(t => [
    t.symbol.replace('.NS',''), t.company_name,
    t.buy_date, t.sell_date,
    t.quantity, t.buy_price, t.sell_price,
    t.holding_days, t.gross_gain, t.gain_type,
  ])
  const csv = [headers, ...rows].map(r => r.join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = 'tax_trades.csv'; a.click()
  URL.revokeObjectURL(url)
}

export default function TradeBreakdownTable({ breakdown }) {
  const [filter,  setFilter]  = useState('All')
  const [sortKey, setSortKey] = useState('sell_date')
  const [sortDir, setSortDir] = useState('desc')

  const trades = breakdown?.trades || []

  const filtered = useMemo(() => {
    let list = [...trades]
    if (filter === 'STCG')   list = list.filter(t => t.gain_type === 'STCG'      && t.gross_gain >= 0)
    if (filter === 'LTCG')   list = list.filter(t => t.gain_type === 'LTCG'      && t.gross_gain >= 0)
    if (filter === 'Losses') list = list.filter(t => t.gross_gain < 0)
    if (filter === 'Debt')   list = list.filter(t => t.gain_type === 'DEBT_SLAB')

    list.sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey]
      if (typeof va === 'string') va = va.toLowerCase()
      if (typeof vb === 'string') vb = vb.toLowerCase()
      return sortDir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1)
    })
    return list
  }, [trades, filter, sortKey, sortDir])

  const toggle = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const SortIcon = ({ k }) => sortKey === k
    ? (sortDir === 'asc' ? <ChevronUp size={10} /> : <ChevronDown size={10} />)
    : null

  if (!trades.length) return (
    <div className="flex flex-col items-center justify-center h-40 gap-2">
      <p className="text-muted text-sm">No sell transactions in this financial year</p>
    </div>
  )

  return (
    <div className="space-y-3">
      {/* Filter + Export bar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex gap-1">
          {FILTERS.map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                filter === f ? 'bg-accent/20 text-accent border border-accent/30' : 'bg-surface text-muted border border-border hover:text-slate-300'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <button
          onClick={() => exportCSV(filtered)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface border border-border text-muted hover:text-slate-300 text-xs transition-colors"
        >
          <Download size={11} /> Export CSV
        </button>
      </div>

      <div className="text-muted text-[10px]">{filtered.length} trades</div>

      <div className="overflow-x-auto rounded-xl border border-border" style={{ background: '#0a0f1c' }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-[10px] uppercase tracking-wider">
              <th className="text-left px-4 py-3 cursor-pointer hover:text-slate-300" onClick={() => toggle('symbol')}>
                <span className="flex items-center gap-1">Symbol <SortIcon k="symbol" /></span>
              </th>
              <th className="text-left px-3 py-3">Company</th>
              <th className="text-right px-3 py-3 cursor-pointer hover:text-slate-300" onClick={() => toggle('quantity')}>
                <span className="flex items-center gap-1 justify-end">Qty <SortIcon k="quantity" /></span>
              </th>
              <th className="text-right px-3 py-3 cursor-pointer hover:text-slate-300" onClick={() => toggle('buy_date')}>
                <span className="flex items-center gap-1 justify-end">Buy Date <SortIcon k="buy_date" /></span>
              </th>
              <th className="text-right px-3 py-3 cursor-pointer hover:text-slate-300" onClick={() => toggle('sell_date')}>
                <span className="flex items-center gap-1 justify-end">Sell Date <SortIcon k="sell_date" /></span>
              </th>
              <th className="text-right px-3 py-3">Holding</th>
              <th className="text-right px-3 py-3">Buy ₹</th>
              <th className="text-right px-3 py-3">Sell ₹</th>
              <th className="text-right px-3 py-3 cursor-pointer hover:text-slate-300" onClick={() => toggle('gross_gain')}>
                <span className="flex items-center gap-1 justify-end">Gain/Loss <SortIcon k="gross_gain" /></span>
              </th>
              <th className="text-center px-3 py-3">Type</th>
              <th className="text-right px-3 py-3">Est. Tax</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.map((t, i) => {
              const isLoss = t.gross_gain < 0
              const estTax = t.gain_type === 'STCG' && !t.is_slab_taxed && t.gross_gain > 0
                ? t.gross_gain * 0.20 * 1.04
                : t.gain_type === 'LTCG' && t.gross_gain > 0
                  ? null   // calculated at portfolio level
                  : isLoss
                    ? t.gross_gain * 0.20 * 1.04  // tax saved
                    : null

              return (
                <tr
                  key={i}
                  className={`hover:bg-white/2 transition-colors ${
                    isLoss ? 'bg-red-500/3' : t.gross_gain > 0 ? 'bg-profit/3' : ''
                  }`}
                >
                  <td className="px-4 py-2.5 font-semibold text-slate-200 text-xs">
                    {t.symbol.replace('.NS', '')}
                  </td>
                  <td className="px-3 py-2.5 text-muted text-xs max-w-[120px] truncate">
                    {t.company_name}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-muted text-xs">{t.quantity}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-muted text-xs">{fmtDate(t.buy_date)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-muted text-xs">{fmtDate(t.sell_date)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-muted text-xs">{fmtHolding(t.holding_days)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-300 text-xs">{formatINR(t.buy_price)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-300 text-xs">{formatINR(t.sell_price)}</td>
                  <td className={`px-3 py-2.5 text-right tabular-nums font-semibold text-xs ${isLoss ? 'text-loss' : 'text-profit'}`}>
                    {formatINR(t.gross_gain, 0)}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <TypeBadge trade={t} />
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-xs">
                    {estTax === null ? (
                      <span className="text-muted">—</span>
                    ) : isLoss ? (
                      <span className="text-profit text-[10px]">Saves {formatINR(-estTax, 0)}</span>
                    ) : (
                      <span className="text-amber-400">{formatINR(estTax, 0)}</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Totals footer */}
      {breakdown?.totals && (
        <div className="flex flex-wrap gap-4 px-2 text-xs text-muted">
          <span>STCG: <span className="text-amber-400 font-semibold">{formatINR(breakdown.totals.stcg, 0)}</span></span>
          <span>LTCG: <span className="text-blue-400 font-semibold">{formatINR(breakdown.totals.ltcg, 0)}</span></span>
          <span>Losses: <span className="text-profit font-semibold">{formatINR(breakdown.totals.total_losses, 0)}</span></span>
          <span>Net Gains: <span className="text-slate-200 font-semibold">{formatINR(breakdown.totals.total_gains, 0)}</span></span>
        </div>
      )}
    </div>
  )
}
