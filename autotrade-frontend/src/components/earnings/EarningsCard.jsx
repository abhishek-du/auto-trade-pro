import { useNavigate } from 'react-router-dom'
import ToneIndicator from './ToneIndicator'
import { ExternalLink } from 'lucide-react'

export default function EarningsCard({ summary, compact = false }) {
  const nav = useNavigate()
  if (!summary) return null

  const { symbol, company_name, quarter, call_date, management_tone, financial_highlights = [],
          revenue_guidance, is_ai, source, ai_confidence } = summary

  const ticker = symbol?.replace('.NS', '').replace('.BO', '')

  return (
    <div
      onClick={() => nav(`/earnings?symbol=${symbol}`)}
      className="rounded-xl border border-border p-4 space-y-3 cursor-pointer hover:border-accent/30 hover:bg-white/[0.02] transition-all glass-panel"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-100 font-bold text-sm">{ticker}</span>
            <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-accent/15 text-cyan border border-accent/20">{quarter}</span>
            {is_ai && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">AI</span>}
          </div>
          <p className="text-muted text-xs mt-0.5 truncate">{company_name}</p>
          {call_date && <p className="text-muted/60 text-[10px]">{call_date} · {source}</p>}
        </div>
        <ToneIndicator tone={management_tone} compact />
      </div>

      {financial_highlights[0] && (
        <p className="text-slate-300 text-xs leading-relaxed line-clamp-2">{financial_highlights[0]}</p>
      )}

      {revenue_guidance && (
        <div className="flex items-center gap-1.5 text-xs text-emerald-400">
          <span>📈</span>
          <span className="truncate">{revenue_guidance}</span>
        </div>
      )}

      <div className="flex items-center justify-between">
        <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border ${
          ai_confidence === 'HIGH' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
          ai_confidence === 'MEDIUM' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' :
          'bg-slate-500/10 text-muted border-border'
        }`}>{ai_confidence}</span>
        <ExternalLink size={11} className="text-muted/50" />
      </div>
    </div>
  )
}
