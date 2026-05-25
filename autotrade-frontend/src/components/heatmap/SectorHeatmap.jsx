import { useState } from 'react'
import { Treemap, BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { getHeatmapColor, getChangePctLabel, SECTOR_COLORS } from '../../utils/heatmapColors'

// ── Sector tile (grid view) ───────────────────────────────────────────────────

function SectorTile({ sector, onClick }) {
  const [hovered, setHovered] = useState(false)
  const colors  = getHeatmapColor(sector.avg_change_pct)
  const isMajor = ['IT', 'Banking', 'Energy'].includes(sector.sector_key)

  return (
    <div
      onClick={() => onClick(sector.sector_key)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background:   colors.bg,
        border:       `1px solid ${colors.border}`,
        borderRadius: '12px',
        padding:      '14px',
        cursor:       'pointer',
        transition:   'transform 0.15s, box-shadow 0.15s',
        transform:    hovered ? 'scale(1.02)' : 'scale(1)',
        boxShadow:    hovered ? '0 4px 20px rgba(0,0,0,0.3)' : 'none',
        gridColumn:   isMajor ? 'span 2' : 'span 1',
      }}
    >
      {/* Name + change */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <div style={{ color: colors.text }} className="text-sm font-bold leading-tight">{sector.short}</div>
          {sector.index_change_pct != null && (
            <div className="text-[10px] mt-0.5" style={{ color: colors.text, opacity: 0.7 }}>
              {sector.index_symbol?.replace('^', '')} {getChangePctLabel(sector.index_change_pct)}
            </div>
          )}
        </div>
        <div style={{ color: colors.text }} className="text-lg font-extrabold tabular-nums leading-none">
          {getChangePctLabel(sector.avg_change_pct)}
        </div>
      </div>

      {/* Mini breadth bar */}
      <div className="flex h-1.5 rounded-full overflow-hidden mb-2">
        {sector.advances > 0 && (
          <div className="bg-green-400/70" style={{ width: `${sector.advances / sector.total * 100}%` }} />
        )}
        {sector.declines > 0 && (
          <div className="bg-red-400/70" style={{ width: `${sector.declines / sector.total * 100}%` }} />
        )}
        {sector.unchanged > 0 && (
          <div className="bg-slate-500/50" style={{ width: `${sector.unchanged / sector.total * 100}%` }} />
        )}
      </div>

      {/* Stats + gainer/loser */}
      <div className="flex items-end justify-between">
        <div style={{ color: colors.text }} className="text-[10px] opacity-80">
          <span className="text-green-300">↑{sector.advances}</span>
          {' '}
          <span className="text-red-300">↓{sector.declines}</span>
          {' '}
          <span>/ {sector.total}</span>
        </div>
        {isMajor && (
          <div className="text-right text-[9px]" style={{ color: colors.text, opacity: 0.75 }}>
            <div className="text-green-300">▲ {sector.top_gainer?.symbol?.replace('.NS', '')} {getChangePctLabel(sector.top_gainer?.change_pct)}</div>
            <div className="text-red-300">▼ {sector.top_loser?.symbol?.replace('.NS', '')} {getChangePctLabel(sector.top_loser?.change_pct)}</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Treemap view ──────────────────────────────────────────────────────────────

function CustomTreeContent({ x, y, width, height, name, avg_change_pct }) {
  const colors = getHeatmapColor(avg_change_pct || 0)
  if (width < 50 || height < 35) return null
  return (
    <g>
      <rect x={x} y={y} width={width} height={height}
        fill={colors.bg} stroke={colors.border} strokeWidth={1} rx={6} />
      <text x={x + width / 2} y={y + height / 2 - 6}
        textAnchor="middle" fill={colors.text}
        fontSize={Math.min(14, width / 5)} fontWeight="600">
        {name}
      </text>
      <text x={x + width / 2} y={y + height / 2 + 10}
        textAnchor="middle" fill={colors.text}
        fontSize={Math.min(13, width / 6)}>
        {getChangePctLabel(avg_change_pct)}
      </text>
    </g>
  )
}

// ── Bars view ─────────────────────────────────────────────────────────────────

function BarsView({ sectors, onClick }) {
  const sorted = [...sectors].sort((a, b) => b.avg_change_pct - a.avg_change_pct)
  const maxAbs = Math.max(...sorted.map(s => Math.abs(s.avg_change_pct)), 1)

  return (
    <div className="space-y-2">
      {sorted.map(s => {
        const colors = getHeatmapColor(s.avg_change_pct)
        const barPct = (Math.abs(s.avg_change_pct) / maxAbs) * 45
        const isPos  = s.avg_change_pct >= 0

        return (
          <div key={s.sector_key}
            onClick={() => onClick(s.sector_key)}
            className="flex items-center gap-3 hover:bg-white/[0.03] px-2 py-1.5 rounded-lg cursor-pointer transition-colors">
            <div className="w-16 text-xs font-semibold text-slate-300 shrink-0">{s.short}</div>
            <div className="flex-1 flex items-center" style={{ gap: 4 }}>
              {/* Negative bar (left of center) */}
              <div className="flex-1 flex justify-end">
                {!isPos && (
                  <div style={{ width: `${barPct}%`, background: colors.bg, border: `1px solid ${colors.border}`, height: 22, borderRadius: 4 }} />
                )}
              </div>
              {/* Center axis */}
              <div className="w-px h-6 bg-border/60 shrink-0" />
              {/* Positive bar (right of center) */}
              <div className="flex-1 flex justify-start">
                {isPos && (
                  <div style={{ width: `${barPct}%`, background: colors.bg, border: `1px solid ${colors.border}`, height: 22, borderRadius: 4 }} />
                )}
              </div>
            </div>
            <div className="w-14 text-right text-xs font-bold tabular-nums" style={{ color: colors.text }}>
              {getChangePctLabel(s.avg_change_pct)}
            </div>
            <div className="w-12 text-right text-[10px] text-muted">
              <span className="text-profit">{s.advances}↑</span> <span className="text-loss">{s.declines}↓</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SectorHeatmap({ sectors = [], onSectorClick, viewMode = 'grid' }) {
  if (sectors.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-muted text-sm">
        No sector data available
      </div>
    )
  }

  if (viewMode === 'treemap') {
    const treeData = sectors.map(s => ({
      name:           s.short,
      size:           s.total || 1,
      avg_change_pct: s.avg_change_pct,
      sector_key:     s.sector_key,
    }))
    return (
      <div style={{ height: 380 }} onClick={e => {
        // Treemap click handled inside CustomTreeContent via parent
      }}>
        <ResponsiveContainer width="100%" height="100%">
          <Treemap
            data={treeData}
            dataKey="size"
            content={<CustomTreeContent />}
            onClick={(d) => d?.sector_key && onSectorClick(d.sector_key)}
          />
        </ResponsiveContainer>
      </div>
    )
  }

  if (viewMode === 'bars') {
    return <BarsView sectors={sectors} onClick={onSectorClick} />
  }

  // Grid view (default)
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(4, 1fr)',
      gap: '10px',
    }}>
      {sectors.map(sector => (
        <SectorTile
          key={sector.sector_key}
          sector={sector}
          onClick={onSectorClick}
        />
      ))}
    </div>
  )
}
