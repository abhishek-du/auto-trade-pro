import { useState } from 'react'
import { ChevronDown, ChevronUp, AlertTriangle, Info, CheckCircle, Circle } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

const SEV_CONFIG = {
  CRITICAL: { border: 'border-red-500/40',   bg: 'bg-red-500/10',    text: 'text-red-400',    badge: 'bg-red-500/20 text-red-400',   Icon: AlertTriangle },
  WARNING:  { border: 'border-amber-500/40',  bg: 'bg-amber-500/8',   text: 'text-amber-400',  badge: 'bg-amber-500/20 text-amber-400', Icon: Circle },
  INFO:     { border: 'border-blue-500/40',   bg: 'bg-blue-500/8',    text: 'text-blue-400',   badge: 'bg-blue-500/20 text-blue-400',  Icon: Info },
  GOOD:     { border: 'border-emerald-500/40',bg: 'bg-emerald-500/8', text: 'text-emerald-400',badge: 'bg-emerald-500/20 text-emerald-400', Icon: CheckCircle },
}

function MetricTable({ metric }) {
  const entries = Object.entries(metric).filter(([k, v]) => v !== null && v !== undefined && typeof v !== 'object' && !Array.isArray(v))
  if (!entries.length) return null
  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <table className="w-full text-xs">
        <tbody className="divide-y divide-border">
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td className="px-3 py-1.5 text-muted capitalize">{k.replace(/_/g, ' ')}</td>
              <td className="px-3 py-1.5 text-slate-200 text-right font-mono tabular-nums">
                {typeof v === 'number' && k.includes('value') ? formatINR(v) :
                 typeof v === 'number' && (k.includes('pct') || k.includes('pct')) ? `${v.toFixed(1)}%` :
                 typeof v === 'number' ? v.toFixed(2) : String(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function FindingCard({ finding, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [checkedActions, setCheckedActions] = useState(new Set())

  const cfg = SEV_CONFIG[finding.severity] || SEV_CONFIG.INFO
  const { Icon } = cfg

  function toggleAction(i) {
    setCheckedActions(prev => {
      const n = new Set(prev)
      n.has(i) ? n.delete(i) : n.add(i)
      return n
    })
  }

  return (
    <div className={`rounded-xl border-l-4 ${cfg.border} border border-border overflow-hidden`} style={{ background: '#0F1829' }}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/3 transition-colors text-left"
      >
        <Icon size={15} className={cfg.text} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded ${cfg.badge}`}>
              {finding.module.replace(/_/g, ' ')}
            </span>
            <span className={`text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded border ${cfg.border} ${cfg.text}`}>
              {finding.severity}
            </span>
          </div>
          <p className="text-slate-200 text-sm font-semibold mt-1 leading-snug">{finding.title}</p>
        </div>
        {expanded ? <ChevronUp size={14} className="text-muted shrink-0" /> : <ChevronDown size={14} className="text-muted shrink-0" />}
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3">
          <p className="text-slate-300 text-xs leading-relaxed">{finding.detail}</p>

          {finding.metric && Object.keys(finding.metric).length > 0 && (
            <MetricTable metric={finding.metric} />
          )}

          {finding.stocks?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {finding.stocks.map(s => (
                <span key={s} className="px-2 py-0.5 rounded-full bg-cyan/10 text-cyan text-[10px] font-bold border border-cyan/20">
                  {s.replace('.NS', '').replace('.BO', '')}
                </span>
              ))}
            </div>
          )}

          {finding.actions?.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Recommended Actions</p>
              {finding.actions.map((action, i) => (
                <button
                  key={i}
                  onClick={() => toggleAction(i)}
                  className={`w-full flex items-start gap-2.5 px-3 py-2 rounded-lg border text-xs text-left transition-all ${
                    checkedActions.has(i)
                      ? 'border-emerald-500/30 bg-emerald-500/8 text-muted line-through'
                      : 'border-border hover:border-accent/30 hover:bg-white/5 text-slate-300'
                  }`}
                >
                  <span className={`mt-0.5 text-[10px] font-bold shrink-0 ${checkedActions.has(i) ? 'text-emerald-400' : 'text-muted'}`}>
                    {checkedActions.has(i) ? '✓' : `${i + 1}.`}
                  </span>
                  {action}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
