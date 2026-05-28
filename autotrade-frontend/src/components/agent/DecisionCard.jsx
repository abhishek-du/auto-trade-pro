import { useState } from 'react'
import { ChevronDown, ChevronUp, ArrowUp, ArrowDown, Ban } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

const ACTION_COLORS = {
  BUY:  { bg: 'bg-emerald-500/15', border: 'border-emerald-500/30', text: 'text-emerald-400', Icon: ArrowUp },
  SELL: { bg: 'bg-red-500/15',     border: 'border-red-500/30',     text: 'text-red-400',     Icon: ArrowDown },
  HOLD: { bg: 'bg-slate-500/15',   border: 'border-slate-500/30',   text: 'text-muted',        Icon: Ban },
}

export default function DecisionCard({ decision }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = ACTION_COLORS[decision.action] || ACTION_COLORS.HOLD
  const { bg, border, text, Icon } = cfg

  const ticker = decision.symbol?.replace('.NS', '').replace('.BO', '')
  const blocked = !!decision.skip_reason

  return (
    <div className={`rounded-xl border-l-4 ${border} border border-border overflow-hidden`} style={{ background: '#0F1829' }}>
      <button onClick={() => setExpanded(!expanded)} className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/3 transition-colors text-left">
        <div className={`p-1.5 rounded-lg ${bg}`}>
          <Icon size={14} className={text} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded ${bg} ${text}`}>{decision.action}</span>
            <span className="text-slate-200 font-bold text-sm">{ticker}</span>
            <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border border-accent/20 text-cyan/80">{decision.strategy}</span>
            {blocked && (
              <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border border-red-500/30 text-red-400 bg-red-500/10">
                BLOCKED: {decision.skip_reason}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-[10px] text-muted mt-0.5">
            <span>conf <span className="text-slate-200 font-semibold">{decision.confidence}%</span></span>
            <span>regime <span className="text-slate-200">{decision.regime}</span></span>
            {decision.entry && <span>@ <span className="text-cyan">₹{decision.entry?.toFixed(2)}</span></span>}
          </div>
        </div>
        {expanded ? <ChevronUp size={14} className="text-muted" /> : <ChevronDown size={14} className="text-muted" />}
      </button>

      {expanded && (
        <div className="px-4 pb-3 space-y-2">
          {decision.entry && (
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded bg-emerald-500/5 border border-emerald-500/20 px-2 py-1.5">
                <p className="text-muted text-[9px] uppercase">Entry</p>
                <p className="text-emerald-400 font-bold tabular-nums">₹{decision.entry.toFixed(2)}</p>
              </div>
              <div className="rounded bg-red-500/5 border border-red-500/20 px-2 py-1.5">
                <p className="text-muted text-[9px] uppercase">Stop</p>
                <p className="text-red-400 font-bold tabular-nums">₹{decision.stop?.toFixed(2)}</p>
              </div>
              <div className="rounded bg-cyan/5 border border-cyan/20 px-2 py-1.5">
                <p className="text-muted text-[9px] uppercase">Target</p>
                <p className="text-cyan font-bold tabular-nums">₹{decision.target?.toFixed(2)}</p>
              </div>
            </div>
          )}
          <div className="flex items-center gap-3 text-[10px] text-muted flex-wrap">
            {decision.qty > 0 && <span>Qty: <span className="text-slate-200">{decision.qty}</span></span>}
            {decision.risk_pct != null && <span>Risk: <span className="text-amber-400">{(decision.risk_pct * 100).toFixed(2)}%</span></span>}
            <span>Macro: <span className={decision.macro_bias > 0 ? 'text-emerald-400' : decision.macro_bias < 0 ? 'text-red-400' : 'text-slate-300'}>{decision.macro_bias > 0 ? '+' : ''}{decision.macro_bias}</span></span>
            {decision.fund_score != null && <span>Fund: <span className="text-slate-300">{decision.fund_score}</span></span>}
          </div>
          {decision.reasons?.length > 0 && (
            <div className="space-y-1">
              <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Reasoning</p>
              <div className="flex flex-wrap gap-1">
                {decision.reasons.map((r, i) => (
                  <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-slate-300 border border-border">{r}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
