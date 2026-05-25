import { useState } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { ASSET_CLASSES } from '../../hooks/useAllocation'
import { formatINR } from '../../utils/indianFormat'

const ORDER = ['large_cap', 'mid_cap', 'small_cap', 'international', 'gold', 'debt', 'cash', 'other']

function HoldingRow({ h }) {
  return (
    <tr className="hover:bg-white/2 transition-colors">
      <td className="px-4 py-2">
        <p className="text-slate-200 text-xs font-semibold">{h.full_name || h.name}</p>
        <p className="text-muted text-[10px]">{h.name !== h.full_name ? h.name : ''}</p>
      </td>
      <td className="px-3 py-2">
        <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${h.type === 'stock' ? 'bg-blue-500/15 text-blue-400' : 'bg-purple-500/15 text-purple-400'}`}>
          {h.type === 'stock' ? 'Stock' : h.category || 'MF'}
        </span>
      </td>
      <td className="px-3 py-2 text-right text-xs tabular-nums text-slate-300">{formatINR(h.value, 0)}</td>
      <td className="px-3 py-2 text-right text-xs tabular-nums text-muted">{h.weight_in_class?.toFixed(1)}%</td>
      {h.pnl_pct !== undefined && (
        <td className={`px-3 py-2 text-right text-xs tabular-nums ${h.pnl_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
          {h.pnl_pct > 0 ? '+' : ''}{h.pnl_pct?.toFixed(1)}%
        </td>
      )}
    </tr>
  )
}

function ClassSection({ cls, data }) {
  const [open, setOpen] = useState(data.value > 0)
  const cfg = ASSET_CLASSES[cls] || {}

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/2 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: cfg.color }} />
          <span className="text-slate-200 text-sm font-semibold">{cfg.label || cls}</span>
          <span className="text-muted text-xs">{data.holdings?.length || 0} positions</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-slate-200 text-sm font-bold tabular-nums">{formatINR(data.value, 0)}</span>
          <span className="text-muted text-xs">{data.total_pct?.toFixed(1)}%</span>
          {open ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
        </div>
      </button>

      {open && (
        data.holdings?.length ? (
          <div className="overflow-x-auto border-t border-border/40" style={{ background: '#060a14' }}>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted text-[9px] uppercase tracking-wider border-b border-border/30">
                  <th className="text-left px-4 py-2">Name</th>
                  <th className="text-left px-3 py-2">Type</th>
                  <th className="text-right px-3 py-2">Value</th>
                  <th className="text-right px-3 py-2">% Class</th>
                  <th className="text-right px-3 py-2">P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/20">
                {[...data.holdings].sort((a, b) => b.value - a.value).map((h, i) => (
                  <HoldingRow key={i} h={h} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="px-4 py-3 text-muted text-xs italic border-t border-border/30" style={{ background: '#060a14' }}>
            No {cfg.label?.toLowerCase() || cls} holdings in portfolio
          </div>
        )
      )}
    </div>
  )
}

export default function HoldingsBreakdown({ allocation }) {
  if (!allocation) return null

  const hasAny = Object.values(allocation).some(v => v.value > 0)
  if (!hasAny) return (
    <div className="flex items-center justify-center h-32 text-muted text-sm">No holdings to display</div>
  )

  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      {ORDER.map(cls => {
        const data = allocation[cls]
        if (!data) return null
        return <ClassSection key={cls} cls={cls} data={data} />
      })}
    </div>
  )
}
