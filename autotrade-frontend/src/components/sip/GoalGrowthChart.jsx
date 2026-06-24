import { useEffect, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts'

function fmtL(v) {
  if (v >= 1e7)  return `${(v / 1e7).toFixed(1)}Cr`
  if (v >= 1e5)  return `${(v / 1e5).toFixed(1)}L`
  if (v >= 1000) return `${(v / 1000).toFixed(0)}K`
  return v.toFixed(0)
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="glass-panel border border-border rounded-lg px-3 py-2 text-xs space-y-1 shadow-xl">
      <p className="text-muted font-medium">{label}</p>
      {payload.map(p => (
        <p key={p.dataKey} style={{ color: p.color }}>
          {p.name}: ₹{(p.value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
        </p>
      ))}
    </div>
  )
}

export default function GoalGrowthChart({ goalId, targetAmount, getProjection }) {
  const [data,    setData]    = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!goalId) return
    setLoading(true)
    getProjection(goalId)
      .then(proj => {
        if (!proj?.scenarios) return
        const pts = proj.scenarios.moderate?.data_points || []
        setData(pts.map(p => ({
          month: `M${p.month}`,
          invested: p.invested,
          corpus:   p.corpus,
        })))
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [goalId])

  if (loading) return (
    <div className="h-64 flex items-center justify-center text-muted text-sm">Loading chart…</div>
  )
  if (!data.length) return (
    <div className="h-64 flex items-center justify-center text-muted text-sm">No projection data yet</div>
  )

  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="gradCorpus" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.02} />
            </linearGradient>
            <linearGradient id="gradInvest" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#64748b" stopOpacity={0.2} />
              <stop offset="95%" stopColor="#64748b" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="month" tick={{ fill: '#64748b', fontSize: 9 }} interval="preserveStartEnd" />
          <YAxis
            tickFormatter={fmtL}
            tick={{ fill: '#64748b', fontSize: 9 }}
            width={48}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend iconType="circle" iconSize={8}
            formatter={v => <span className="text-xs text-muted capitalize">{v}</span>} />
          {targetAmount && (
            <ReferenceLine
              y={targetAmount}
              stroke="#22c55e"
              strokeDasharray="6 3"
              label={{ value: 'Target', position: 'insideTopRight', fill: '#22c55e', fontSize: 10 }}
            />
          )}
          <Area
            type="monotone" dataKey="invested" name="Invested"
            stroke="#64748b" strokeWidth={1.5}
            fill="url(#gradInvest)"
          />
          <Area
            type="monotone" dataKey="corpus" name="Projected Value"
            stroke="#3b82f6" strokeWidth={2}
            fill="url(#gradCorpus)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
