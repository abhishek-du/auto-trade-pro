export default function AdvanceDeclineBar({ advances = 0, declines = 0, unchanged = 0, total, label }) {
  const tot     = total || (advances + declines + unchanged) || 1
  const advPct  = (advances  / tot) * 100
  const decPct  = (declines  / tot) * 100
  const uncPct  = (unchanged / tot) * 100
  const adRatio = advances / Math.max(declines, 1)

  return (
    <div className="space-y-2">
      {label && (
        <div className="flex items-center justify-between">
          <span className="text-slate-300 text-xs font-semibold">{label}</span>
          <span className="text-muted text-[10px]">{tot.toLocaleString('en-IN')} stocks</span>
        </div>
      )}

      {/* Stacked bar */}
      <div className="flex h-7 rounded-md overflow-hidden">
        {advPct > 0 && (
          <div className="bg-profit transition-all duration-500 flex items-center justify-center"
            style={{ width: `${advPct}%` }}>
            {advPct > 12 && <span className="text-[10px] font-bold text-white/90">{Math.round(advPct)}%</span>}
          </div>
        )}
        {decPct > 0 && (
          <div className="bg-loss transition-all duration-500 flex items-center justify-center"
            style={{ width: `${decPct}%` }}>
            {decPct > 12 && <span className="text-[10px] font-bold text-white/90">{Math.round(decPct)}%</span>}
          </div>
        )}
        {uncPct > 0 && (
          <div className="bg-slate-600 transition-all duration-500 flex items-center justify-center"
            style={{ width: `${uncPct}%` }}>
          </div>
        )}
        {advPct === 0 && decPct === 0 && (
          <div className="bg-slate-700 w-full flex items-center justify-center">
            <span className="text-muted text-[10px]">No data</span>
          </div>
        )}
      </div>

      {/* Stats row */}
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-profit font-semibold">
          ▲ {advances.toLocaleString('en-IN')} <span className="text-muted font-normal">advances</span>
        </span>
        <span className={`font-semibold tabular-nums ${adRatio >= 1 ? 'text-profit' : 'text-loss'}`}>
          A/D {adRatio.toFixed(2)}:1
        </span>
        <span className="text-loss font-semibold">
          <span className="text-muted font-normal">declines</span> {declines.toLocaleString('en-IN')} ▼
        </span>
      </div>
      {unchanged > 0 && (
        <div className="text-center text-[10px] text-muted">— {unchanged} unchanged</div>
      )}
    </div>
  )
}
