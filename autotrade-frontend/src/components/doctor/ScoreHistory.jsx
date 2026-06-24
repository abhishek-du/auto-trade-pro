import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

function HistTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  return (
    <div className="rounded-lg border border-border px-3 py-2 text-xs glass-panel">
      <p className="text-muted mb-0.5">{d?.date}</p>
      <p className="text-slate-100 font-bold">Score: {d?.score} (Grade {d?.grade})</p>
    </div>
  )
}

export default function ScoreHistory({ history = [] }) {
  if (history.length < 2) {
    return (
      <div className="rounded-xl border border-border p-5 flex flex-col items-center justify-center gap-2 h-full glass-panel">
        <p className="text-muted text-sm font-semibold">Score History</p>
        <p className="text-muted/60 text-xs text-center">Run again next month to see score trend</p>
      </div>
    )
  }

  const data = history.map(h => ({
    date:  new Date(h.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }),
    score: h.score,
    grade: h.grade,
  }))

  const first = data[0]?.score || 0
  const last  = data[data.length - 1]?.score || 0
  const improving = last >= first

  return (
    <div className="rounded-xl border border-border p-5 space-y-3 h-full glass-panel">
      <div className="flex items-center justify-between">
        <p className="text-slate-200 font-semibold text-sm">Score History</p>
        <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${improving ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
          {improving ? '▲ Improving' : '▼ Declining'}
        </span>
      </div>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <XAxis dataKey="date" tick={{ fill: '#4E6280', fontSize: 9 }} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={{ fill: '#4E6280', fontSize: 9 }} axisLine={false} tickLine={false} width={28} />
            <ReferenceLine y={70} stroke="#1E2D45" strokeDasharray="3 3" />
            <Tooltip content={<HistTooltip />} />
            <Line
              type="monotone"
              dataKey="score"
              stroke={improving ? '#10B981' : '#EF4444'}
              strokeWidth={2}
              dot={{ fill: improving ? '#10B981' : '#EF4444', r: 4 }}
              label={({ x, y, value, index }) => (
                <text x={x} y={y - 8} textAnchor="middle" fill="#94A3B8" fontSize={9}>
                  {data[index]?.grade}
                </text>
              )}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
