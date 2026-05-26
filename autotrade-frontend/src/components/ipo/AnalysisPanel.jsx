import { useState } from 'react'
import { CheckCircle, AlertTriangle, Zap, RefreshCw } from 'lucide-react'

const VERDICT_STYLES = {
  SUBSCRIBE: { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.4)', text: '#10B981', label: 'SUBSCRIBE' },
  AVOID:     { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.4)',  text: '#EF4444', label: 'AVOID'     },
  NEUTRAL:   { bg: 'rgba(100,116,139,0.12)',border: 'rgba(100,116,139,0.3)',text: '#94A3B8', label: 'NEUTRAL'   },
}

const CONVICTION_COLORS = {
  HIGH:   '#10B981',
  MEDIUM: '#F59E0B',
  LOW:    '#94A3B8',
}

export default function AnalysisPanel({ analysis, onRefresh, loading = false }) {
  if (!analysis) {
    return (
      <div className="rounded-xl border border-border p-5 flex flex-col items-center gap-3" style={{ background: '#0a0f1c' }}>
        <Zap size={20} className="text-muted/40" />
        <p className="text-muted text-sm">No analysis yet</p>
        {onRefresh && (
          <button onClick={onRefresh} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/10 text-accent text-xs hover:bg-accent/20 transition-colors">
            <RefreshCw size={12} /> Generate Analysis
          </button>
        )}
      </div>
    )
  }

  const { verdict = 'NEUTRAL', conviction, score, summary, positives = [], concerns = [], strategy = {}, source } = analysis
  const vs = VERDICT_STYLES[verdict] || VERDICT_STYLES.NEUTRAL

  return (
    <div className="space-y-3">
      {/* Verdict banner */}
      <div className="rounded-xl border p-4 space-y-2" style={{ background: vs.bg, borderColor: vs.border }}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <span className="text-2xl font-black tracking-wider" style={{ color: vs.text }}>{vs.label}</span>
            {conviction && (
              <span className="text-xs font-bold px-2 py-0.5 rounded-full border" style={{ color: CONVICTION_COLORS[conviction], borderColor: CONVICTION_COLORS[conviction] + '40', background: CONVICTION_COLORS[conviction] + '15' }}>
                {conviction} conviction
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-muted text-[10px]">Score</span>
            <span className="text-xl font-black tabular-nums" style={{ color: score >= 7 ? '#10B981' : score >= 5 ? '#F59E0B' : '#EF4444' }}>
              {score}<span className="text-muted text-sm font-normal">/10</span>
            </span>
            {onRefresh && (
              <button onClick={onRefresh} disabled={loading} className="p-1.5 rounded-lg border border-border text-muted hover:text-slate-300 transition-colors">
                <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
              </button>
            )}
          </div>
        </div>
        <p className="text-slate-300 text-xs leading-relaxed">{summary}</p>
        {source === 'rule_based' && (
          <p className="text-muted/60 text-[10px]">Rule-based analysis (Groq key not configured)</p>
        )}
      </div>

      {/* Positives + Concerns */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded-xl border border-border p-3 space-y-1.5" style={{ background: '#0a0f1c' }}>
          <p className="text-profit text-[10px] font-semibold uppercase tracking-widest flex items-center gap-1">
            <CheckCircle size={10} /> Positives
          </p>
          {positives.map((p, i) => (
            <p key={i} className="text-slate-300 text-xs leading-snug flex gap-1.5">
              <span className="text-profit mt-0.5 shrink-0">+</span>{p}
            </p>
          ))}
        </div>
        <div className="rounded-xl border border-border p-3 space-y-1.5" style={{ background: '#0a0f1c' }}>
          <p className="text-loss text-[10px] font-semibold uppercase tracking-widest flex items-center gap-1">
            <AlertTriangle size={10} /> Concerns
          </p>
          {concerns.map((c, i) => (
            <p key={i} className="text-slate-300 text-xs leading-snug flex gap-1.5">
              <span className="text-loss mt-0.5 shrink-0">!</span>{c}
            </p>
          ))}
        </div>
      </div>

      {/* Strategy */}
      {(strategy.listing_play || strategy.long_term) && (
        <div className="rounded-xl border border-accent/20 p-3 space-y-2" style={{ background: 'rgba(59,130,246,0.04)' }}>
          <p className="text-accent text-[10px] font-semibold uppercase tracking-widest">Strategy</p>
          {strategy.listing_play && (
            <div>
              <p className="text-muted text-[10px]">Listing play</p>
              <p className="text-slate-300 text-xs">{strategy.listing_play}</p>
            </div>
          )}
          {strategy.long_term && (
            <div>
              <p className="text-muted text-[10px]">Long term</p>
              <p className="text-slate-300 text-xs">{strategy.long_term}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
