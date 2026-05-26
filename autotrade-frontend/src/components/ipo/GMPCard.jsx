export default function GMPCard({ ipo, compact = false }) {
  const gmpPct   = ipo?.gmp_pct
  const gmpInr   = ipo?.gmp_inr   ?? 0
  const upper    = ipo?.price_upper ?? 0
  const estPrice = ipo?.estimated_listing_price

  if (!gmpPct && !gmpInr) {
    return (
      <div className={`rounded-lg border border-border text-center ${compact ? 'px-3 py-2' : 'px-4 py-3'}`} style={{ background: '#0a0f1c' }}>
        <p className="text-muted text-[10px]">GMP</p>
        <p className="text-slate-500 text-xs mt-0.5">Not available</p>
        {!compact && <p className="text-muted/50 text-[10px] mt-0.5">GMP on paid plans only</p>}
      </div>
    )
  }

  const isPositive = gmpPct > 0
  const color      = gmpPct >= 20 ? '#10B981' : gmpPct >= 5 ? '#22D3EE' : gmpPct < 0 ? '#EF4444' : '#94A3B8'
  const barWidth   = Math.min(Math.abs(gmpPct), 100)

  return (
    <div className={`rounded-lg border border-border ${compact ? 'px-3 py-2' : 'px-4 py-3 space-y-2'}`} style={{ background: '#0a0f1c' }}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-muted text-[10px] uppercase tracking-widest">GMP</p>
        <span className="text-xs font-bold tabular-nums" style={{ color }}>
          {isPositive ? '+' : ''}{gmpPct?.toFixed(1)}%
        </span>
      </div>
      {!compact && (
        <>
          <div className="h-1.5 bg-surface rounded-full overflow-hidden">
            <div className="h-full rounded-full transition-all" style={{ width: `${barWidth}%`, background: color }} />
          </div>
          <div className="flex justify-between text-[10px] text-muted">
            <span>{isPositive ? '+' : ''}₹{gmpInr.toFixed(0)} premium</span>
            {estPrice && <span>Est. ₹{estPrice.toFixed(0)}</span>}
          </div>
          {upper > 0 && estPrice && (
            <p className="text-[10px] text-muted/60">
              Issue price ₹{upper.toFixed(0)} → Est. listing ₹{estPrice.toFixed(0)}
            </p>
          )}
        </>
      )}
    </div>
  )
}
