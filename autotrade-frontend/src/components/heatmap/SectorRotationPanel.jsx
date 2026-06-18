import { getChangePctLabel } from '../../utils/heatmapColors'

export default function SectorRotationPanel({ rotation }) {
  if (!rotation) {
    return (
      <div className="bg-panel border border-border rounded-xl p-4">
        <div className="text-muted text-xs text-center py-8">No rotation data yet</div>
      </div>
    )
  }

  const { nifty_change_pct, outperforming = [], underperforming = [], rotation_note } = rotation

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center gap-3 flex-wrap">
        <h3 className="text-slate-200 text-sm font-semibold">Sector Rotation vs NIFTY 50</h3>
        <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${
          nifty_change_pct >= 0
            ? 'bg-profit/10 text-profit border-profit/30'
            : 'bg-loss/10 text-loss border-loss/30'
        }`}>
          NIFTY {getChangePctLabel(nifty_change_pct)}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-border">
        {/* Outperforming */}
        <div className="p-4">
          <div className="text-profit text-[10px] font-semibold uppercase tracking-wider mb-3">
            Outperforming NIFTY ▲
          </div>
          {outperforming.length === 0 ? (
            <div className="text-muted text-xs">None outperforming</div>
          ) : (
            <div className="space-y-2">
              {outperforming.map(s => (
                <div key={s.sector} className="flex items-center justify-between text-xs">
                  <span className="text-slate-300 font-semibold w-16">{s.short}</span>
                  <span className="text-profit tabular-nums">{getChangePctLabel(s.avg_change_pct)}</span>
                  <span className="text-emerald-400 tabular-nums text-[10px]">+{s.vs_nifty.toFixed(2)}% vs idx</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Underperforming */}
        <div className="p-4">
          <div className="text-loss text-[10px] font-semibold uppercase tracking-wider mb-3">
            Underperforming NIFTY ▼
          </div>
          {underperforming.length === 0 ? (
            <div className="text-muted text-xs">None underperforming</div>
          ) : (
            <div className="space-y-2">
              {underperforming.map(s => (
                <div key={s.sector} className="flex items-center justify-between text-xs">
                  <span className="text-slate-300 font-semibold w-16">{s.short}</span>
                  <span className="text-loss tabular-nums">{getChangePctLabel(s.avg_change_pct)}</span>
                  <span className="text-red-400 tabular-nums text-[10px]">{s.vs_nifty.toFixed(2)}% vs idx</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Rotation note */}
      {rotation_note && (
        <div className="px-4 py-3 border-t border-border bg-accent/5">
          <p className="text-slate-300 text-xs leading-relaxed">{rotation_note}</p>
        </div>
      )}
    </div>
  )
}
