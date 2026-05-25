import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

function fmtTime(isoStr) {
  try {
    return new Date(isoStr).toLocaleTimeString('en-IN', {
      timeZone: 'Asia/Kolkata',
      hour:     '2-digit',
      minute:   '2-digit',
      hour12:   false,
    })
  } catch {
    return isoStr
  }
}

export default function BreadthHistory({ historyData = [] }) {
  if (!historyData || historyData.length === 0) {
    return (
      <div className="bg-panel border border-border rounded-xl p-4">
        <h3 className="text-slate-200 text-sm font-semibold mb-3">Today's Breadth Timeline</h3>
        <div className="flex items-center justify-center h-28 text-muted text-xs">
          No intraday breadth history yet
        </div>
      </div>
    )
  }

  const chartData = historyData.map(d => ({
    time:     fmtTime(d.timestamp),
    advances: d.watchlist_advances ?? d.advances ?? 0,
    declines: d.watchlist_declines ?? d.declines ?? 0,
    ratio:    d.ad_ratio ?? 1,
  }))

  return (
    <div className="bg-panel border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-slate-200 text-sm font-semibold">Today's Breadth Timeline</h3>
        <span className="text-muted text-[10px]">{historyData.length} readings</span>
      </div>
      <ResponsiveContainer width="100%" height={120}>
        <AreaChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="advGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#10B981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#10B981" stopOpacity={0}   />
            </linearGradient>
            <linearGradient id="decGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#EF4444" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#EF4444" stopOpacity={0}   />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: '#0F1829', border: '1px solid #1E293B', borderRadius: '8px', fontSize: 11 }}
            formatter={(val, name) => [val, name === 'advances' ? '▲ Advances' : '▼ Declines']}
          />
          <Area type="monotone" dataKey="advances" stroke="#10B981" strokeWidth={1.5} fill="url(#advGrad)" />
          <Area type="monotone" dataKey="declines" stroke="#EF4444" strokeWidth={1.5} fill="url(#decGrad)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
