import { useState } from 'react'
import { RefreshCw, Info } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

const FILTERS = ['ALL', 'BUY', 'SELL']

export default function TransactionsTab({ transactions, onRefresh }) {
  const [filter, setFilter] = useState('ALL')

  const visible = (transactions || []).filter(tx =>
    filter === 'ALL' || tx.tx_type === filter
  )

  if (!transactions?.length) {
    return (
      <div className="text-center py-12 text-muted text-sm">
        No trade history yet.
      </div>
    )
  }

  return (
    <div>
      {/* Info banner */}
      <div className="flex items-start gap-2 mb-3 px-3 py-2 rounded-lg bg-accent/5 border border-accent/15">
        <Info size={13} className="text-accent shrink-0 mt-0.5" />
        <p className="text-[11px] text-muted leading-relaxed">
          <span className="text-slate-300 font-semibold">BUY</span> entries are auto-recorded when you add a holding.{' '}
          <span className="text-slate-300 font-semibold">SELL</span> entries are created when you sell shares.
          This history is used for XIRR and tax calculations.
        </p>
      </div>

      <div className="flex items-center justify-between mb-3">
        {/* Filter pills */}
        <div className="flex items-center gap-1">
          {FILTERS.map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 rounded text-[11px] font-semibold transition-colors ${
                filter === f
                  ? f === 'BUY'  ? 'bg-profit/20 text-profit'
                  : f === 'SELL' ? 'bg-loss/20 text-loss'
                  : 'bg-accent/20 text-accent'
                  : 'text-muted hover:text-slate-300'
              }`}
            >
              {f}
            </button>
          ))}
          <span className="text-muted text-[10px] ml-1">{visible.length} entries</span>
        </div>
        <button onClick={onRefresh} className="p-1.5 rounded text-muted hover:text-white hover:bg-white/5 transition-colors">
          <RefreshCw size={13} />
        </button>
      </div>

      {visible.length === 0 ? (
        <div className="text-center py-8 text-muted text-sm">
          No {filter} transactions found.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Date', 'Stock', 'Type', 'Qty', 'Price', 'Amount', 'STT', 'Notes'].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {visible.map(tx => (
                <tr key={tx.id} className="hover:bg-white/[0.02] transition-colors">
                  <td className="px-3 py-2.5 text-muted text-xs whitespace-nowrap">{tx.trade_date}</td>
                  <td className="px-3 py-2.5">
                    <p className="text-slate-200 font-semibold text-xs">{tx.symbol?.replace('.NS', '')}</p>
                    <p className="text-muted text-[10px] truncate max-w-[100px]">{tx.company_name}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase ${
                      tx.tx_type === 'BUY' ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
                    }`}>{tx.tx_type}</span>
                  </td>
                  <td className="px-3 py-2.5 text-slate-300 tabular-nums text-xs">{tx.quantity}</td>
                  <td className="px-3 py-2.5 text-slate-300 tabular-nums text-xs font-mono">{formatINR(tx.price)}</td>
                  <td className="px-3 py-2.5 text-slate-200 tabular-nums text-xs font-semibold">{formatINR(tx.total_amount)}</td>
                  <td className="px-3 py-2.5 text-muted tabular-nums text-xs">{tx.stt ? formatINR(tx.stt) : '—'}</td>
                  <td className="px-3 py-2.5 text-muted text-xs max-w-[120px] truncate">{tx.notes || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
