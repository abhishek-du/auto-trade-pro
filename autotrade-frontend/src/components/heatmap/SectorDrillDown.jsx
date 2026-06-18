import { useState } from 'react'
import { X, ExternalLink } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { getHeatmapColor, getChangePctLabel, SECTOR_COLORS } from '../../utils/heatmapColors'
import { formatINR, formatVolume } from '../../utils/indianFormat'

function WeightBar({ pct }) {
  return (
    <div className="flex items-center gap-1.5 min-w-[70px]">
      <div className="flex-1 h-1 bg-border/40 rounded-full overflow-hidden">
        <div className="h-full bg-accent/50 rounded-full" style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="text-[10px] text-muted tabular-nums w-8 text-right">{pct?.toFixed(1)}%</span>
    </div>
  )
}

export default function SectorDrillDown({ sector, onClose }) {
  const navigate = useNavigate()
  const [sortBy, setSortBy] = useState('weight')

  if (!sector) return null

  const colors     = getHeatmapColor(sector.avg_change_pct)
  const accentColor = SECTOR_COLORS[sector.sector_key]?.accent || '#3B82F6'

  const stocks = [...(sector.stocks || [])].sort((a, b) => {
    if (sortBy === 'weight')  return (b.market_cap_cr || 0) - (a.market_cap_cr || 0)
    if (sortBy === 'change')  return (b.change_pct || 0)   - (a.change_pct || 0)
    if (sortBy === 'volume')  return (b.volume || 0)       - (a.volume || 0)
    return 0
  })

  const chartData = stocks.slice(0, 10).map(s => ({
    name:   (s.symbol || '').replace('.NS', ''),
    change: s.change_pct || 0,
  }))

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed top-0 right-0 w-full sm:w-[480px] h-full z-50 overflow-y-auto"
        style={{ background: '#0A1120', boxShadow: '-4px 0 24px rgba(0,0,0,0.4)' }}>

        {/* Header */}
        <div className="sticky top-0 z-10 border-b border-border px-5 py-4"
          style={{ background: '#0A1120' }}>
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-slate-100 text-lg font-bold">{sector.name}</h2>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <span className={`text-base font-extrabold tabular-nums`}
                  style={{ color: colors.text }}>
                  {getChangePctLabel(sector.avg_change_pct)}
                </span>
                <span className="text-muted text-xs">
                  {sector.advances}↑ · {sector.declines}↓
                  {sector.unchanged > 0 && ` · ${sector.unchanged}—`}
                  {' '}of {sector.total} stocks
                </span>
              </div>
            </div>
            <button onClick={onClose}
              className="p-1.5 rounded-lg text-muted hover:text-slate-200 hover:bg-white/5 transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="p-5 space-y-5">

          {/* Mini bar chart */}
          {chartData.length > 0 && (
            <div>
              <div className="text-xs text-muted font-semibold mb-2">Stock Performance (%)</div>
              <ResponsiveContainer width="100%" height={110}>
                <BarChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                  <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 9 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#64748b', fontSize: 9 }} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#0F1829', border: '1px solid #1E293B', borderRadius: 8, fontSize: 11 }}
                    formatter={(v) => [v.toFixed(2) + '%', 'Change']}
                  />
                  <Bar dataKey="change" radius={[3, 3, 0, 0]}>
                    {chartData.map((d, i) => {
                      const c = getHeatmapColor(d.change)
                      return <Cell key={i} fill={c.bg} stroke={c.border} />
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Sort controls */}
          <div className="flex items-center gap-2">
            <span className="text-muted text-[10px] font-semibold uppercase tracking-wider">Sort by:</span>
            {['weight', 'change', 'volume'].map(s => (
              <button key={s}
                onClick={() => setSortBy(s)}
                className={`text-[10px] px-2 py-0.5 rounded font-semibold capitalize transition-colors ${
                  sortBy === s ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'
                }`}>
                {s === 'weight' ? 'Mkt Cap' : s}
              </button>
            ))}
          </div>

          {/* Stocks table */}
          <div className="space-y-1 overflow-x-auto scrollbar-none pb-2">
            {stocks.map(s => {
              const sc = getHeatmapColor(s.change_pct || 0)
              return (
                <div key={s.symbol}
                  style={{ borderLeft: `3px solid ${sc.bg}` }}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-r-lg hover:bg-white/[0.04] transition-colors min-w-max">
                  {/* Symbol */}
                  <div className="w-24 shrink-0">
                    <div className="text-slate-200 text-xs font-bold">{(s.symbol || '').replace('.NS', '')}</div>
                    <div className="text-muted text-[9px] truncate">{s.name}</div>
                  </div>
                  {/* Price */}
                  <div className="w-20 text-right shrink-0">
                    <div className="text-slate-100 text-xs font-semibold tabular-nums">{formatINR(s.price)}</div>
                  </div>
                  {/* Change% */}
                  <div className="w-16 text-right shrink-0">
                    <span className="text-xs font-bold tabular-nums" style={{ color: sc.text }}>
                      {getChangePctLabel(s.change_pct)}
                    </span>
                  </div>
                  {/* Weight bar */}
                  <div className="flex-1">
                    <WeightBar pct={s.weight_pct} />
                  </div>
                  {/* Volume */}
                  <div className="w-12 text-right text-[10px] text-muted tabular-nums shrink-0">
                    {formatVolume(s.volume)}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Footer */}
          <div className="pt-2 border-t border-border flex items-center justify-between">
            <div className="text-muted text-[11px]">
              Sector Index: <span className="text-slate-300 font-mono">{sector.index_symbol}</span>
              {sector.index_price && (
                <span className="ml-1 text-slate-300">{formatINR(sector.index_price)}</span>
              )}
              {sector.index_change_pct != null && (
                <span className={`ml-1 font-semibold ${sector.index_change_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {getChangePctLabel(sector.index_change_pct)}
                </span>
              )}
            </div>
            <button
              onClick={() => navigate(`/chart?symbol=${encodeURIComponent(sector.index_symbol)}&name=${encodeURIComponent(sector.name)}`)}
              className="flex items-center gap-1 text-[11px] text-accent hover:underline">
              View chart <ExternalLink size={10} />
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
