import { RefreshCw } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

export default function TransactionsTab({ transactions, onRefresh }) {
  if (!transactions?.length) {
    return (
      <div className="text-center py-12 text-muted text-sm">
        No transactions recorded yet.
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-muted text-xs">{transactions.length} transactions</p>
        <button onClick={onRefresh} className="p-1.5 rounded text-muted hover:text-white hover:bg-white/5 transition-colors">
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Date', 'Stock', 'Type', 'Qty', 'Price', 'Amount', 'STT'].map(h => (
                <th key={h} className="text-left px-3 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {transactions.map(tx => (
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
