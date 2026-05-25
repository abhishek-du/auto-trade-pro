import { RefreshCw, TrendingUp, Calendar, Target, Zap } from 'lucide-react'

function fmtINR(n) {
  if (n == null) return '—'
  if (n >= 1e7)  return `₹${(n / 1e7).toFixed(2)}Cr`
  if (n >= 1e5)  return `₹${(n / 1e5).toFixed(2)}L`
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function fmtPct(n) {
  if (n == null) return '—'
  return `${+n >= 0 ? '+' : ''}${(+n).toFixed(1)}%`
}

function ProgressRing({ pct, size = 120 }) {
  const r     = (size - 12) / 2
  const circ  = 2 * Math.PI * r
  const dash  = circ * Math.min(pct, 100) / 100
  const cx    = size / 2
  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={cx} cy={cx} r={r} fill="none" stroke="#1e293b" strokeWidth={10} />
      <circle
        cx={cx} cy={cx} r={r} fill="none"
        stroke={pct >= 100 ? '#22c55e' : '#3b82f6'}
        strokeWidth={10}
        strokeDasharray={`${dash} ${circ}`}
        strokeLinecap="round"
        style={{ transition: 'stroke-dasharray 0.8s ease' }}
      />
    </svg>
  )
}

function MetricCard({ label, value, sub, color = 'text-slate-100' }) {
  return (
    <div className="rounded-lg border border-border p-3 space-y-0.5" style={{ background: '#0a0f1c' }}>
      <p className="text-muted text-[9px] uppercase tracking-widest">{label}</p>
      <p className={`font-bold text-base tabular-nums ${color}`}>{value}</p>
      {sub && <p className="text-muted text-[10px]">{sub}</p>}
    </div>
  )
}

export default function GoalProgressPanel({ goal, onRefresh, refreshing }) {
  if (!goal) return null

  const prog = Math.min(100, goal.progress_pct || 0)

  return (
    <div className="space-y-5">
      {/* Top: ring + metrics */}
      <div className="flex flex-col sm:flex-row items-center gap-6">
        {/* Ring */}
        <div className="relative flex-shrink-0">
          <ProgressRing pct={prog} size={130} />
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <p className={`font-bold text-xl tabular-nums ${prog >= 100 ? 'text-profit' : 'text-accent'}`}>
              {prog.toFixed(1)}%
            </p>
            <p className="text-muted text-[9px]">complete</p>
          </div>
        </div>

        {/* Metrics grid */}
        <div className="flex-1 grid grid-cols-2 gap-3 w-full">
          <MetricCard
            label="Current Value"
            value={fmtINR(goal.current_value)}
            sub={`Invested: ${fmtINR(goal.total_invested)}`}
            color="text-profit"
          />
          <MetricCard
            label="Target"
            value={fmtINR(goal.target_amount)}
            sub={`Due: ${goal.target_date}`}
            color="text-slate-100"
          />
          <MetricCard
            label="Total Gain"
            value={fmtINR(goal.total_gain)}
            sub={fmtPct(goal.gain_pct)}
            color={goal.total_gain >= 0 ? 'text-profit' : 'text-loss'}
          />
          <MetricCard
            label="XIRR"
            value={goal.xirr != null ? `${goal.xirr.toFixed(1)}%` : '—'}
            sub={`${goal.months_elapsed}mo elapsed`}
            color={goal.xirr > 0 ? 'text-profit' : 'text-muted'}
          />
        </div>
      </div>

      {/* On-track badge */}
      {goal.monthly_sip > 0 && (
        <div className={`flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold ${
          goal.on_track ? 'bg-profit/10 text-profit border border-profit/20' : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
        }`}>
          <Zap size={14} />
          {goal.on_track
            ? `On track! Projected end: ${goal.projected_end || '—'}`
            : `Off track — needs adjustment. Projected: ${goal.projected_end || '—'}`}
        </div>
      )}

      {/* 3-scenario projection bars */}
      {goal.scenarios && (
        <div className="space-y-2">
          <p className="text-muted text-[10px] uppercase tracking-widest flex items-center gap-1.5">
            <Target size={10} /> 3-Scenario Projection
          </p>
          {Object.entries(goal.scenarios).map(([label, s]) => {
            const pct = Math.min(100, (s.projected / goal.target_amount) * 100)
            return (
              <div key={label} className="space-y-1">
                <div className="flex items-center justify-between text-[10px]">
                  <span className="text-muted capitalize">{label} ({s.return_pct}%/yr)</span>
                  <span className={`font-semibold ${s.hits_target ? 'text-profit' : 'text-loss'}`}>
                    {fmtINR(s.projected)} {s.hits_target ? '✓' : '✗'}
                  </span>
                </div>
                <div className="h-1.5 bg-surface rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${s.hits_target ? 'bg-profit' : 'bg-amber-500'}`}
                    style={{ width: `${pct}%`, transition: 'width 0.6s ease' }}
                  />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Refresh button */}
      <button
        onClick={onRefresh}
        disabled={refreshing}
        className="flex items-center gap-2 text-xs text-muted hover:text-accent transition-colors disabled:opacity-50"
      >
        <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} />
        {refreshing ? 'Refreshing NAVs…' : 'Refresh NAVs'}
      </button>
    </div>
  )
}
