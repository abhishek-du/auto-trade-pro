import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { formatINR } from '../../utils/indianFormat'

const COLORS = [
  '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b',
  '#10b981', '#ef4444', '#6366f1', '#14b8a6', '#f97316',
]

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="bg-panel border border-border rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="text-slate-200 font-semibold">{d.name || d.sector}</p>
      <p className="text-muted">{formatINR(d.value)} · {d.weight?.toFixed(1)}%</p>
    </div>
  )
}

function DonutChart({ data, nameKey, title }) {
  if (!data?.length) return (
    <div className="flex items-center justify-center h-44 text-muted text-xs">No data</div>
  )
  return (
    <div>
      <p className="text-muted text-xs font-semibold uppercase tracking-wider mb-3">{title}</p>
      <ResponsiveContainer width="100%" height={180}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={75}
            paddingAngle={2}
            dataKey="value"
          >
            {data.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="transparent" />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      {/* Legend */}
      <div className="mt-2 space-y-1">
        {data.slice(0, 6).map((d, i) => (
          <div key={i} className="flex items-center justify-between text-[10px]">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: COLORS[i % COLORS.length] }} />
              <span className="text-muted truncate max-w-[100px]">{d[nameKey] || d.name || d.sector}</span>
            </div>
            <span className="text-slate-400 tabular-nums">{d.weight?.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function AllocationCharts({ allocation }) {
  if (!allocation) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
      <DonutChart data={allocation.by_stock}  nameKey="name"   title="By Stock" />
      <DonutChart data={allocation.by_sector} nameKey="sector" title="By Sector" />
    </div>
  )
}
