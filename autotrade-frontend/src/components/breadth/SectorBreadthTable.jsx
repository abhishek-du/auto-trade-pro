function moodDot(adRatio) {
  if (adRatio >= 1.5) return { dot: 'bg-profit', label: 'Bullish',  cls: 'text-profit' }
  if (adRatio >= 0.8) return { dot: 'bg-warn',   label: 'Neutral',  cls: 'text-warn'   }
  return                     { dot: 'bg-loss',   label: 'Bearish',  cls: 'text-loss'   }
}

export default function SectorBreadthTable({ byIndex }) {
  if (!byIndex || Object.keys(byIndex).length === 0) return null

  const rows = Object.entries(byIndex).map(([name, d]) => {
    const adv   = d.advances  || 0
    const dec   = d.declines  || 0
    const unc   = d.unchanged || 0
    const ratio = adv / Math.max(dec, 1)
    return { name, adv, dec, unc, ratio }
  }).sort((a, b) => b.ratio - a.ratio)

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-slate-200 text-sm font-semibold">Breadth by Index</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border bg-panel/80">
              <th className="px-3 py-2 text-left text-muted text-[10px] font-semibold uppercase tracking-wider">Index</th>
              <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">Advances</th>
              <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">Declines</th>
              <th className="px-3 py-2 text-right text-muted text-[10px] font-semibold uppercase tracking-wider">A/D Ratio</th>
              <th className="px-3 py-2 text-center text-muted text-[10px] font-semibold uppercase tracking-wider">Mood</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ name, adv, dec, unc, ratio }) => {
              const m = moodDot(ratio)
              return (
                <tr key={name} className="border-b border-border/30 hover:bg-white/[0.03]">
                  <td className="px-3 py-2 text-slate-300 font-medium text-[11px]">{name}</td>
                  <td className="px-3 py-2 text-right text-profit font-semibold tabular-nums">{adv}</td>
                  <td className="px-3 py-2 text-right text-loss font-semibold tabular-nums">{dec}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    <span className={ratio >= 1 ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                      {ratio.toFixed(2)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`inline-flex items-center gap-1.5 text-[10px] font-semibold ${m.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
                      {m.label}
                    </span>
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
