import { useState } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { ASSET_CLASSES } from '../../hooks/useAllocation'
import { formatINR } from '../../utils/indianFormat'

const SIZE_MAP = { sm: 160, md: 240, lg: 300 }

function CustomTooltip({ active, payload, targetAllocation }) {
  if (!active || !payload?.length) return null
  const { name, value, pct } = payload[0].payload
  const targetPct = targetAllocation?.[name] ?? null
  const dev       = targetPct !== null ? pct - targetPct : null
  return (
    <div className="rounded-xl border border-border px-3 py-2 text-xs glass-panel">
      <p className="text-slate-200 font-semibold mb-1">{ASSET_CLASSES[name]?.label || name}</p>
      <p className="text-muted">Value: <span className="text-slate-200">{formatINR(value, 0)}</span></p>
      <p className="text-muted">Weight: <span className="text-slate-200">{pct.toFixed(1)}%</span></p>
      {dev !== null && (
        <p className={dev > 0 ? 'text-amber-400' : 'text-profit'}>
          vs Target: {dev > 0 ? '+' : ''}{dev.toFixed(1)}%
        </p>
      )}
    </div>
  )
}

export default function AllocationDonut({ allocation, title, size = 'md', targetAllocation }) {
  const [activeIndex, setActiveIndex] = useState(null)
  const px = SIZE_MAP[size] || 240

  const data = Object.entries(allocation || {})
    .filter(([, v]) => v.value > 0)
    .map(([key, v]) => ({
      name:  key,
      value: v.value,
      pct:   v.total_pct || 0,
      label: ASSET_CLASSES[key]?.label || key,
      color: ASSET_CLASSES[key]?.color || '#64748B',
    }))
    .sort((a, b) => b.value - a.value)

  const total = data.reduce((s, d) => s + d.value, 0)

  if (!data.length) return (
    <div className="flex items-center justify-center rounded-xl border border-border" style={{ width: px, height: px, background: '#0a0f1c' }}>
      <p className="text-muted text-xs">No data</p>
    </div>
  )

  const outerR = px * 0.38
  const innerR = px * 0.24

  return (
    <div className="space-y-3">
      {title && <p className="text-muted text-[10px] uppercase tracking-widest">{title}</p>}

      <div style={{ width: px, height: px }} className="relative">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              cx="50%" cy="50%"
              outerRadius={outerR}
              innerRadius={innerR}
              dataKey="value"
              stroke="none"
              onMouseEnter={(_, i) => setActiveIndex(i)}
              onMouseLeave={() => setActiveIndex(null)}
            >
              {data.map((entry, i) => (
                <Cell
                  key={entry.name}
                  fill={entry.color}
                  opacity={activeIndex === null || activeIndex === i ? 1 : 0.45}
                />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip targetAllocation={targetAllocation} />} />
          </PieChart>
        </ResponsiveContainer>
        {/* Center label */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-slate-100 font-bold" style={{ fontSize: size === 'sm' ? 13 : 15 }}>
            {formatINR(total, 1)}
          </span>
          <span className="text-muted text-[10px] mt-0.5">total</span>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {data.map((d, i) => (
          <button
            key={d.name}
            onClick={() => setActiveIndex(activeIndex === i ? null : i)}
            className="flex items-center gap-1.5 text-[11px] transition-opacity"
            style={{ opacity: activeIndex === null || activeIndex === i ? 1 : 0.4 }}
          >
            <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ background: d.color }} />
            <span className="text-muted">{d.label}</span>
            <span className="text-slate-300 font-semibold">{d.pct.toFixed(1)}%</span>
          </button>
        ))}
      </div>
    </div>
  )
}
