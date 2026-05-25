import { getHeatmapColor, getChangePctLabel } from '../../utils/heatmapColors'

function Cell({ value }) {
  if (value == null) return <td className="px-3 py-2 text-center text-muted text-xs">—</td>
  const colors = getHeatmapColor(value)
  return (
    <td className="px-3 py-2 text-center">
      <span className="text-xs font-semibold tabular-nums px-1.5 py-0.5 rounded text-nowrap"
        style={{ background: colors.bg, color: colors.text, border: `1px solid ${colors.border}` }}>
        {getChangePctLabel(value)}
      </span>
    </td>
  )
}

export default function PerformanceTimeline({ sectors = [] }) {
  if (sectors.length === 0) return null

  const sorted = [...sectors].sort((a, b) => b.avg_change_pct - a.avg_change_pct)

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-slate-200 text-sm font-semibold">Sector Performance Today</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-panel/80">
              <th className="px-3 py-2 text-left text-muted text-[10px] font-semibold uppercase tracking-wider">Sector</th>
              <th className="px-3 py-2 text-center text-muted text-[10px] font-semibold uppercase tracking-wider">Today</th>
              <th className="px-3 py-2 text-center text-muted text-[10px] font-semibold uppercase tracking-wider">Mood</th>
              <th className="px-3 py-2 text-center text-muted text-[10px] font-semibold uppercase tracking-wider">Adv/Dec</th>
              <th className="px-3 py-2 text-center text-muted text-[10px] font-semibold uppercase tracking-wider">Breadth</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(s => {
              const moodCls = {
                STRONGLY_BULLISH: 'text-emerald-400',
                BULLISH:          'text-profit',
                NEUTRAL:          'text-slate-400',
                BEARISH:          'text-loss',
                STRONGLY_BEARISH: 'text-red-400',
              }[s.mood] || 'text-slate-400'

              return (
                <tr key={s.sector_key} className="border-b border-border/30 hover:bg-white/[0.03]">
                  <td className="px-3 py-2">
                    <span className="text-slate-300 text-xs font-semibold">{s.short}</span>
                  </td>
                  <Cell value={s.avg_change_pct} />
                  <td className="px-3 py-2 text-center">
                    <span className={`text-[10px] font-semibold ${moodCls}`}>
                      {(s.mood || '').replace('_', ' ')}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center text-[10px]">
                    <span className="text-profit font-semibold">{s.advances}↑</span>
                    {' '}
                    <span className="text-loss font-semibold">{s.declines}↓</span>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <div className="flex items-center justify-center gap-1.5">
                      <div className="w-12 h-1.5 bg-border/40 rounded-full overflow-hidden">
                        <div className="h-full bg-profit/70 rounded-full"
                          style={{ width: `${s.breadth_pct || 0}%` }} />
                      </div>
                      <span className="text-[10px] text-muted tabular-nums">{s.breadth_pct}%</span>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
