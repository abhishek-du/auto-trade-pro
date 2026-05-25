import { useState } from 'react'
import { LayoutGrid, BarChart2, TreePine, RefreshCw } from 'lucide-react'
import { useSectors }           from '../hooks/useSectors'
import SectorHeatmapComp        from '../components/heatmap/SectorHeatmap'
import SectorDrillDown          from '../components/heatmap/SectorDrillDown'
import SectorRotationPanel      from '../components/heatmap/SectorRotationPanel'
import PerformanceTimeline      from '../components/heatmap/PerformanceTimeline'
import { getHeatmapColor, getChangePctLabel, HEATMAP_LEGEND } from '../utils/heatmapColors'
import { timeSince } from '../utils/indianFormat'

const VIEW_MODES = [
  { id: 'grid',    Icon: LayoutGrid, label: 'Grid'    },
  { id: 'treemap', Icon: TreePine,   label: 'Treemap' },
  { id: 'bars',    Icon: BarChart2,  label: 'Bars'    },
]

function SkeletonTile() {
  return <div className="h-24 rounded-xl bg-slate-800/50 animate-pulse" />
}

export default function SectorHeatmap() {
  const {
    sectors, loading, rotation,
    selectedSector, selectSector,
    refresh, bestSector, worstSector,
  } = useSectors()

  const [viewMode, setViewMode] = useState(() =>
    localStorage.getItem('heatmap_view') || 'grid'
  )

  function changeView(mode) {
    setViewMode(mode)
    localStorage.setItem('heatmap_view', mode)
  }

  const nifty = sectors.find(s => s.index_symbol === '^NSEI')
  const lastUpdated = sectors[0]?.last_updated

  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <LayoutGrid size={18} className="text-cyan" />
            NSE Sector Heatmap
          </h1>
          <p className="text-muted text-sm mt-0.5">
            {sectors.length} sectors · {sectors.reduce((s, x) => s + (x.total || 0), 0)} stocks
          </p>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          {/* Quick chips */}
          {bestSector && (
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-profit/15 text-profit border border-profit/30">
              Best: {bestSector.short} {getChangePctLabel(bestSector.avg_change_pct)}
            </span>
          )}
          {worstSector && worstSector.avg_change_pct < 0 && (
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-loss/15 text-loss border border-loss/30">
              Worst: {worstSector.short} {getChangePctLabel(worstSector.avg_change_pct)}
            </span>
          )}

          {/* View mode switcher */}
          <div className="flex items-center gap-0.5 bg-panel border border-border rounded-lg p-0.5">
            {VIEW_MODES.map(({ id, Icon, label }) => (
              <button
                key={id}
                title={label}
                onClick={() => changeView(id)}
                className={`p-1.5 rounded transition-colors ${
                  viewMode === id
                    ? 'bg-accent/20 text-accent'
                    : 'text-muted hover:text-slate-300'
                }`}>
                <Icon size={14} />
              </button>
            ))}
          </div>

          {lastUpdated && (
            <span className="text-muted text-xs">{timeSince(lastUpdated)}</span>
          )}

          <button onClick={refresh} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs font-medium text-slate-300 hover:text-white hover:border-accent/40 transition-colors disabled:opacity-50">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Color legend */}
      <div className="bg-panel border border-border rounded-xl px-4 py-3">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-muted text-[10px] font-semibold uppercase tracking-wider shrink-0">Scale:</span>
          {HEATMAP_LEGEND.map(({ label, changePct }) => {
            const c = getHeatmapColor(changePct)
            return (
              <div key={label} className="flex items-center gap-1">
                <div className="w-3 h-3 rounded-sm shrink-0" style={{ background: c.bg, border: `1px solid ${c.border}` }} />
                <span className="text-[9px] text-muted whitespace-nowrap">{label}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Main heatmap */}
      <div className="bg-panel border border-border rounded-xl p-4">
        {loading ? (
          <div className="grid grid-cols-4 gap-2.5">
            {Array.from({ length: 10 }).map((_, i) => <SkeletonTile key={i} />)}
          </div>
        ) : sectors.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-muted text-sm gap-2">
            <span>No sector data available</span>
            <button onClick={refresh} className="text-accent text-xs hover:underline">Refresh now</button>
          </div>
        ) : (
          <SectorHeatmapComp
            sectors={sectors}
            onSectorClick={selectSector}
            viewMode={viewMode}
          />
        )}
      </div>

      {/* Rotation + Timeline */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3">
          <SectorRotationPanel rotation={rotation} sectors={sectors} />
        </div>
        <div className="lg:col-span-2">
          <PerformanceTimeline sectors={sectors} />
        </div>
      </div>

      {/* Drill-down panel */}
      {selectedSector && (
        <SectorDrillDown
          sector={selectedSector}
          onClose={() => selectSector(null)}
        />
      )}
    </div>
  )
}
