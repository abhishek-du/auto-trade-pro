import { Target, Home, GraduationCap, Car, Shield, Plane, Heart, TrendingUp, Trash2, ChevronRight } from 'lucide-react'

const GOAL_ICONS = {
  retirement: { icon: Shield,       color: 'text-purple-400',  bg: 'bg-purple-500/10' },
  education:  { icon: GraduationCap,color: 'text-blue-400',    bg: 'bg-blue-500/10' },
  house:      { icon: Home,         color: 'text-amber-400',   bg: 'bg-amber-500/10' },
  vehicle:    { icon: Car,          color: 'text-green-400',   bg: 'bg-green-500/10' },
  emergency:  { icon: Shield,       color: 'text-red-400',     bg: 'bg-red-500/10' },
  travel:     { icon: Plane,        color: 'text-cyan-400',    bg: 'bg-cyan-500/10' },
  wedding:    { icon: Heart,        color: 'text-pink-400',    bg: 'bg-pink-500/10' },
  wealth:     { icon: TrendingUp,   color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
}

function fmtINR(n) {
  if (n == null) return '—'
  if (n >= 1e7)  return `₹${(n / 1e7).toFixed(1)}Cr`
  if (n >= 1e5)  return `₹${(n / 1e5).toFixed(1)}L`
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

export default function GoalCard({ goal, isActive, onClick, onDelete }) {
  const cfg   = GOAL_ICONS[goal.goal_type] || GOAL_ICONS.wealth
  const Icon  = cfg.icon
  const prog  = Math.min(100, goal.progress_pct || 0)
  const today = new Date()
  const tgt   = new Date(goal.target_date)
  const yrs   = ((tgt - today) / (365.25 * 86400000)).toFixed(1)

  return (
    <div
      onClick={onClick}
      className={`relative rounded-xl border p-4 cursor-pointer transition-all ${
        isActive
          ? 'border-accent bg-accent/10'
          : 'border-border glass-panel hover:border-accent/40 hover:bg-white/2'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className={`p-2 rounded-lg ${cfg.bg} flex-shrink-0`}>
          <Icon size={16} className={cfg.color} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-slate-100 font-semibold text-sm truncate">{goal.name}</p>
          <p className="text-muted text-[10px] mt-0.5 capitalize">
            {goal.goal_type} · {yrs > 0 ? `${yrs}y left` : 'overdue'}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={e => { e.stopPropagation(); onDelete(goal.id) }}
            className="p-1 rounded text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <Trash2 size={12} />
          </button>
          <ChevronRight size={14} className={isActive ? 'text-accent' : 'text-muted'} />
        </div>
      </div>

      {/* Progress bar */}
      <div className="mt-3 space-y-1.5">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-muted">{fmtINR(goal.current_value)} of {fmtINR(goal.target_amount)}</span>
          <span className={prog >= 100 ? 'text-profit font-semibold' : 'text-accent'}>{prog.toFixed(1)}%</span>
        </div>
        <div className="h-1.5 bg-surface rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${prog >= 100 ? 'bg-profit' : 'bg-accent'}`}
            style={{ width: `${prog}%` }}
          />
        </div>
      </div>

      <div className="flex items-center justify-between mt-2.5 text-[10px] text-muted">
        <span>₹{(goal.monthly_sip || 0).toLocaleString('en-IN')}/mo</span>
        <span>{goal.installment_count || 0} installments</span>
      </div>
    </div>
  )
}
