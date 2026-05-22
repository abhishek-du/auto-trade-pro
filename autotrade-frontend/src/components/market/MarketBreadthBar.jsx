export default function MarketBreadthBar({ advances = 0, declines = 0, unchanged = 0 }) {
  const total = advances + declines + unchanged || 1;
  const advPct = (advances  / total) * 100;
  const decPct = (declines  / total) * 100;
  const neuPct = (unchanged / total) * 100;

  return (
    <div className="bg-panel border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-slate-200 text-sm font-semibold">Market Breadth</p>
        <span className="text-muted text-xs">{advances + declines + unchanged} stocks</span>
      </div>

      {/* Stacked bar */}
      <div className="flex h-3 rounded-full overflow-hidden gap-px">
        {advPct > 0 && (
          <div className="bg-profit transition-all duration-500" style={{ width: `${advPct}%` }} />
        )}
        {decPct > 0 && (
          <div className="bg-loss transition-all duration-500" style={{ width: `${decPct}%` }} />
        )}
        {neuPct > 0 && (
          <div className="bg-neutral transition-all duration-500" style={{ width: `${neuPct}%` }} />
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-profit shrink-0" />
          <span className="text-profit font-semibold">{advances}</span>
          <span className="text-muted">Advances</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-loss shrink-0" />
          <span className="text-loss font-semibold">{declines}</span>
          <span className="text-muted">Declines</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-neutral shrink-0" />
          <span className="text-slate-400 font-semibold">{unchanged}</span>
          <span className="text-muted">Unchanged</span>
        </div>
      </div>
    </div>
  );
}
