import { timeSince } from '../../utils/indianFormat'

function ScoreArc({ score }) {
  const size   = 120
  const cx     = size / 2
  const cy     = size / 2
  const r      = 48
  const stroke = 10
  const total  = 2 * Math.PI * r
  const filled = (score / 100) * total

  const color =
    score >= 85 ? '#10B981' :
    score >= 70 ? '#22D3EE' :
    score >= 55 ? '#F59E0B' :
    score >= 40 ? '#F97316' : '#EF4444'

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1E2D45" strokeWidth={stroke} />
      <circle
        cx={cx} cy={cy} r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeDasharray={`${filled} ${total - filled}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${cx} ${cy})`}
        style={{ transition: 'stroke-dasharray 0.6s ease' }}
      />
    </svg>
  )
}

export default function HealthScoreCard({ score, grade, summary, generatedAt, quickWins = [], onQuickWinClick }) {
  const gradeColor =
    grade === 'A' ? '#10B981' :
    grade === 'B' ? '#22D3EE' :
    grade === 'C' ? '#F59E0B' :
    grade === 'D' ? '#F97316' : '#EF4444'

  return (
    <div className="rounded-xl border border-border p-5 space-y-4" style={{ background: '#0F1829' }}>
      <div className="flex items-center gap-6">
        {/* Score ring */}
        <div className="relative shrink-0">
          <ScoreArc score={score} />
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="font-bold text-2xl leading-none" style={{ color: gradeColor }}>{grade}</span>
            <span className="text-slate-400 text-[10px] font-mono tabular-nums">{score}/100</span>
          </div>
        </div>

        {/* Right side */}
        <div className="flex-1 min-w-0">
          <p className="text-slate-100 font-semibold text-sm leading-snug mb-1">{summary}</p>
          {generatedAt && (
            <p className="text-muted text-xs">Last checked: {timeSince(generatedAt)}</p>
          )}
        </div>
      </div>

      {/* Quick wins */}
      {quickWins.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Quick Wins</p>
          {quickWins.map((w, i) => (
            <button
              key={i}
              onClick={() => onQuickWinClick?.(i)}
              className="w-full text-left flex items-start gap-2 px-3 py-2 rounded-lg border border-border hover:border-cyan/30 hover:bg-white/5 transition-all text-xs text-slate-300"
            >
              <span className="text-cyan shrink-0 mt-0.5">→</span>
              <span className="line-clamp-2">{w}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
