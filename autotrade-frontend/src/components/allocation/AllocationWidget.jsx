import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PieChart, Pie, Cell } from 'recharts'
import { ExternalLink, ArrowRight } from 'lucide-react'
import { ASSET_CLASSES } from '../../hooks/useAllocation'
import AllocationBars from './AllocationBars'
import { formatINR } from '../../utils/indianFormat'

function MiniDonut({ allocation, size = 100 }) {
  const data = Object.entries(allocation || {})
    .filter(([, v]) => (v.value || v) > 0)
    .map(([key, v]) => ({
      name:  key,
      value: v.value ?? v,
      color: ASSET_CLASSES[key]?.color || '#64748B',
    }))

  if (!data.length) return <div className="rounded-full bg-surface" style={{ width: size, height: size }} />

  const r = size * 0.46
  const i = size * 0.28

  return (
    <PieChart width={size} height={size}>
      <Pie data={data} cx={size/2} cy={size/2} outerRadius={r} innerRadius={i} dataKey="value" stroke="none">
        {data.map((e, idx) => <Cell key={idx} fill={e.color} />)}
      </Pie>
    </PieChart>
  )
}

export default function AllocationWidget({ portfolioId, sipGoalIds = [], compact = false }) {
  const [analysis, setAnalysis] = useState(null)
  const [loading,  setLoading]  = useState(false)

  useEffect(() => {
    if (!portfolioId && sipGoalIds.length === 0) return
    setLoading(true)
    const params = new URLSearchParams({ risk_profile: 'moderate' })
    if (portfolioId) params.set('portfolio_id', portfolioId)
    sipGoalIds.forEach(id => params.append('sip_goal_ids', id))
    fetch(`/api/v1/allocation/analysis?${params}`)
      .then(r => r.json())
      .then(d => { setAnalysis(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [portfolioId, JSON.stringify(sipGoalIds)])

  if (loading) return (
    <div className="animate-pulse rounded-xl border border-border bg-panel p-4 space-y-3">
      <div className="h-3 bg-surface rounded w-1/2" />
      <div className="flex gap-3">
        <div className="w-20 h-20 rounded-full bg-surface" />
        <div className="flex-1 space-y-2 pt-1">
          <div className="h-2 bg-surface rounded" />
          <div className="h-2 bg-surface rounded w-3/4" />
        </div>
      </div>
    </div>
  )

  if (!analysis) return null

  const rs     = analysis.risk_score || {}
  const alloc  = analysis.current_allocation || {}
  const target = analysis.target_allocation  || {}

  if (compact) {
    const topDeviations = (analysis.rebalancing || [])
      .filter(r => r.action !== 'HOLD')
      .slice(0, 3)

    return (
      <div className="rounded-xl border border-border p-4 space-y-3" style={{ background: '#0F1829' }}>
        <div className="flex items-center justify-between">
          <p className="text-slate-200 text-sm font-semibold">Asset Allocation</p>
          <Link to={`/allocation${portfolioId ? `?portfolio=${portfolioId}` : ''}`} className="text-muted hover:text-accent">
            <ExternalLink size={13} />
          </Link>
        </div>

        <div className="flex items-start gap-4">
          <MiniDonut allocation={alloc} size={100} />
          <div className="flex-1 space-y-1.5 pt-1">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-semibold" style={{ color: rs.color }}>
                {rs.score?.toFixed(1)} — {rs.label}
              </span>
            </div>
            <p className="text-muted text-[10px]">{formatINR(analysis.portfolio_total, 0)} total</p>
            <div className="flex flex-wrap gap-1 mt-2">
              {topDeviations.map((d, i) => (
                <span key={i} className="px-1.5 py-0.5 rounded-full text-[9px] font-semibold"
                  style={{
                    background: d.action === 'SELL' ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)',
                    color: d.action === 'SELL' ? '#EF4444' : '#22C55E',
                  }}>
                  {d.action === 'SELL' ? '▲' : '▼'} {d.asset_class.replace('_', ' ')} {d.deviation_pct > 0 ? '+' : ''}{d.deviation_pct.toFixed(0)}%
                </span>
              ))}
            </div>
          </div>
        </div>

        <Link to={`/allocation${portfolioId ? `?portfolio=${portfolioId}` : ''}`}
          className="flex items-center gap-1 text-accent text-xs hover:underline">
          View full analysis <ArrowRight size={11} />
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <AllocationBars
        current={alloc}
        target={target}
        showDeviation={true}
        threshold={5}
      />
      <Link to={`/allocation${portfolioId ? `?portfolio=${portfolioId}` : ''}`}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-accent/30 bg-accent/10 text-accent text-xs font-semibold hover:bg-accent/20 transition-colors">
        Go to Allocation Analyzer <ArrowRight size={12} />
      </Link>
    </div>
  )
}
