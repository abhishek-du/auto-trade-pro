function Bar({ label, value, max }) {
  if (value == null) return null
  const pct    = max > 0 ? Math.min((value / max) * 100, 100) : 0
  const color  = value >= 50 ? '#10B981' : value >= 10 ? '#3B82F6' : value >= 1 ? '#F59E0B' : '#EF4444'
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[10px]">
        <span className="text-muted">{label}</span>
        <span className="font-semibold tabular-nums" style={{ color }}>{value.toFixed(2)}x</span>
      </div>
      <div className="h-1.5 bg-surface rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  )
}

function interpret(total) {
  if (total == null) return { label: 'Awaited', color: '#64748B' }
  if (total >= 50)   return { label: 'Heavily Oversubscribed', color: '#10B981' }
  if (total >= 10)   return { label: 'Strongly Oversubscribed', color: '#3B82F6' }
  if (total >= 3)    return { label: 'Oversubscribed', color: '#22D3EE' }
  if (total >= 1)    return { label: 'Fully Subscribed', color: '#F59E0B' }
  return { label: 'Undersubscribed', color: '#EF4444' }
}

export default function SubscriptionMeter({ subscription, compact = false }) {
  if (!subscription) {
    return (
      <div className="rounded-lg border border-border px-3 py-2 text-center" style={{ background: '#0a0f1c' }}>
        <p className="text-muted text-xs">Subscription data unavailable</p>
      </div>
    )
  }

  const { qib, nii, retail, total } = subscription
  const max     = Math.max(qib ?? 0, nii ?? 0, retail ?? 0, total ?? 0, 1)
  const { label, color } = interpret(total)

  if (compact) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold tabular-nums" style={{ color }}>
          {total != null ? `${total.toFixed(2)}x` : 'TBA'}
        </span>
        <span className="text-[10px] text-muted">{label}</span>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-border p-4 space-y-3" style={{ background: '#0a0f1c' }}>
      <div className="flex items-center justify-between">
        <p className="text-slate-200 text-xs font-semibold">Subscription Status</p>
        {total != null && (
          <span className="text-xs font-bold px-2 py-0.5 rounded-full" style={{ color, background: `${color}18` }}>
            {total.toFixed(2)}x — {label}
          </span>
        )}
      </div>
      <div className="space-y-2.5">
        <Bar label="QIB (Institutional)"   value={qib}    max={max} />
        <Bar label="NII / HNI"             value={nii}    max={max} />
        <Bar label="Retail (Individual)"   value={retail} max={max} />
        {total != null && <Bar label="Total"           value={total}  max={max} />}
      </div>
    </div>
  )
}
