export default function WatchlistAlertsBar({ alertsData, onAlertClick }) {
  if (!alertsData) return null

  const chips = [
    {
      key:   'near_52w_high',
      icon:  '★',
      label: 'near 52W High',
      cls:   'border-amber-400/40 bg-amber-400/10 text-amber-400',
    },
    {
      key:   'near_52w_low',
      icon:  '↓',
      label: 'near 52W Low',
      cls:   'border-loss/40 bg-loss/10 text-loss',
    },
    {
      key:   'high_volume',
      icon:  '⚡',
      label: 'high volume',
      cls:   'border-accent/40 bg-accent/10 text-accent',
    },
    {
      key:   'strong_signals',
      icon:  '▲',
      label: 'strong signals',
      cls:   'border-profit/40 bg-profit/10 text-profit',
    },
    {
      key:   'oversold',
      icon:  '↘',
      label: 'oversold',
      cls:   'border-muted/40 bg-muted/10 text-muted',
    },
  ].filter(c => (alertsData[c.key] || []).length > 0)

  if (chips.length === 0) return null

  return (
    <div className="flex items-center gap-2 overflow-x-auto scrollbar-none pb-0.5">
      <span className="text-muted text-[10px] font-semibold uppercase tracking-wider shrink-0">Alerts</span>
      {chips.map(({ key, icon, label, cls }) => {
        const count = (alertsData[key] || []).length
        return (
          <button
            key={key}
            onClick={() => onAlertClick?.(key, alertsData[key])}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-semibold whitespace-nowrap shrink-0 transition-opacity hover:opacity-80 ${cls}`}
          >
            <span>{icon}</span>
            <span>{count} {label}</span>
          </button>
        )
      })}
    </div>
  )
}
