import ToneIndicator from './ToneIndicator'

const TONE_ORDER = { OPTIMISTIC: 4, NEUTRAL: 3, CAUTIOUS: 2, NEGATIVE: 1 }

function DeltaIcon({ prev, curr }) {
  if (!prev || !curr) return <span className="text-muted">—</span>
  const p = TONE_ORDER[prev] || 3
  const c = TONE_ORDER[curr] || 3
  if (c > p) return <span className="text-emerald-400">▲</span>
  if (c < p) return <span className="text-red-400">▼</span>
  return <span className="text-muted">—</span>
}

export default function ComparisonView({ summaries = [] }) {
  if (summaries.length < 2) return null

  const rows = [
    { label: 'Revenue Guidance', key: 'revenue_guidance' },
    { label: 'Margin Guidance',  key: 'margin_guidance'  },
    { label: 'Capex Guidance',   key: 'capex_guidance'   },
    { label: 'Tone',             key: 'management_tone', isTone: true },
    { label: 'AI Confidence',    key: 'ai_confidence'    },
  ]

  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="px-5 py-3 border-b border-border">
        <h3 className="text-slate-200 font-semibold text-sm">Quarter Comparison</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-2.5 text-left text-muted font-semibold uppercase tracking-wide">Metric</th>
              {summaries.map(s => (
                <th key={s.quarter} className="px-4 py-2.5 text-left text-cyan font-bold">{s.quarter}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {rows.map(({ label, key, isTone }) => (
              <tr key={key} className="hover:bg-white/[0.02]">
                <td className="px-4 py-2.5 text-muted font-medium whitespace-nowrap">{label}</td>
                {summaries.map((s, i) => (
                  <td key={s.quarter} className="px-4 py-2.5">
                    {isTone ? (
                      <div className="flex items-center gap-1.5">
                        <ToneIndicator tone={s[key]} compact />
                        {i > 0 && <DeltaIcon prev={summaries[i-1][key]} curr={s[key]} />}
                      </div>
                    ) : (
                      <span className={s[key] ? 'text-slate-300' : 'text-muted/50 italic'}>
                        {s[key] || 'Not disclosed'}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
            {/* Top financial highlight row */}
            <tr className="hover:bg-white/[0.02]">
              <td className="px-4 py-2.5 text-muted font-medium">Top Highlight</td>
              {summaries.map(s => (
                <td key={s.quarter} className="px-4 py-2.5 text-slate-300 max-w-xs">
                  <p className="line-clamp-2 text-[10px] leading-relaxed">
                    {(s.financial_highlights || [])[0] || '—'}
                  </p>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
