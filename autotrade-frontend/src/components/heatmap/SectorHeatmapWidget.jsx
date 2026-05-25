import { useEffect, useState } from 'react'
import { getHeatmapColor, getChangePctLabel } from '../../utils/heatmapColors'

function useSectorSummary() {
  const [sectors, setSectors] = useState([])
  useEffect(() => {
    const load = () =>
      fetch('/api/v1/india/sectors/summary')
        .then(r => r.json())
        .then(d => setSectors(Array.isArray(d) ? d : []))
        .catch(() => {})
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])
  return sectors
}

function CompactTile({ sector }) {
  const colors = getHeatmapColor(sector.avg_change_pct)
  return (
    <div className="rounded-lg p-2.5 flex flex-col items-center gap-1 cursor-default transition-colors"
      style={{ background: colors.bg, border: `1px solid ${colors.border}` }}>
      <span className="text-[10px] font-bold" style={{ color: colors.text }}>{sector.short}</span>
      <span className="text-[11px] font-extrabold tabular-nums" style={{ color: colors.text }}>
        {getChangePctLabel(sector.avg_change_pct)}
      </span>
    </div>
  )
}

function FullTile({ sector }) {
  const colors = getHeatmapColor(sector.avg_change_pct)
  return (
    <div className="rounded-xl p-3 flex flex-col gap-1.5 cursor-default"
      style={{ background: colors.bg, border: `1px solid ${colors.border}` }}>
      <div className="flex justify-between items-start">
        <span className="text-xs font-bold" style={{ color: colors.text }}>{sector.short}</span>
        <span className="text-sm font-extrabold tabular-nums" style={{ color: colors.text }}>
          {getChangePctLabel(sector.avg_change_pct)}
        </span>
      </div>
      <div className="flex h-1 rounded-full overflow-hidden">
        {sector.advances > 0 && (
          <div className="bg-green-400/60" style={{ width: `${sector.advances / sector.total * 100}%` }} />
        )}
        {sector.declines > 0 && (
          <div className="bg-red-400/60" style={{ width: `${sector.declines / sector.total * 100}%` }} />
        )}
      </div>
      <div className="text-[9px]" style={{ color: colors.text, opacity: 0.7 }}>
        {sector.advances}↑ {sector.declines}↓
      </div>
    </div>
  )
}

export default function SectorHeatmapWidget({ compact = false, maxSectors = 10 }) {
  const sectors = useSectorSummary()
  const display = sectors.slice(0, maxSectors)

  if (display.length === 0) {
    return (
      <div className="text-muted text-xs text-center py-4">Loading sectors…</div>
    )
  }

  if (compact) {
    return (
      <div className="grid gap-1.5" style={{ gridTemplateColumns: `repeat(${Math.min(display.length, 5)}, 1fr)` }}>
        {display.map(s => <CompactTile key={s.sector_key} sector={s} />)}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
      {display.map(s => <FullTile key={s.sector_key} sector={s} />)}
    </div>
  )
}
