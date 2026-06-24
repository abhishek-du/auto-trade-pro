import { useState, useEffect } from 'react'
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  PieChart, Pie, Cell,
  BarChart, Bar,
  LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
  ReferenceLine,
} from 'recharts'
import {
  TrendingUp, TrendingDown, Scale, Target, ArrowRightLeft,
  Sliders, RefreshCw, AlertTriangle, CheckCircle, Info, Activity,
} from 'lucide-react'
import { apiFetch } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import { formatINR } from '../utils/indianFormat'

// ── palette ───────────────────────────────────────────────────────────────────
const SECTOR_COLORS = [
  '#6366F1','#10B981','#F59E0B','#EF4444','#3B82F6','#EC4899',
  '#8B5CF6','#14B8A6','#F97316','#84CC16','#06B6D4','#D946EF',
]
const CHART_THEME = {
  grid: { strokeDasharray: '3 3', stroke: '#334155', strokeOpacity: 0.5 },
  tick: { fill: '#64748B', fontSize: 10 },
  axis: { stroke: '#334155' },
}

// ── helpers ───────────────────────────────────────────────────────────────────
const fmtPct = (n) => (n == null || isNaN(n) ? '—' : `${Number(n).toFixed(2)}%`)
const fmtNum = (n) => (n == null || isNaN(n) ? '—' : Number(n).toFixed(3))
const fmtDate = (d) => d ? new Date(d).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : '—'

function StatCard({ label, value, sub, highlight, icon: Icon, tooltip }) {
  return (
    <div className="glass-panel rounded-xl p-4 space-y-1 relative group hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.4)] transition-all duration-300">
      {tooltip && (
        <div className="hidden group-hover:block absolute top-2 right-2 z-10 w-56 bg-slate-800 border border-border text-xs text-slate-300 p-2 rounded-lg shadow-xl">
          {tooltip}
        </div>
      )}
      <div className="flex items-center gap-2">
        {Icon && <Icon size={14} className="text-muted shrink-0" />}
        <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">{label}</p>
        {tooltip && <Info size={11} className="text-muted/50 ml-auto" />}
      </div>
      <p className={`font-bold text-xl tabular-nums ${highlight ?? 'text-slate-100'}`}>{value ?? '—'}</p>
      {sub && <p className="text-muted text-xs">{sub}</p>}
    </div>
  )
}

function SectionHeader({ title, sub }) {
  return (
    <div className="mb-3">
      <h2 className="text-slate-100 font-semibold text-sm">{title}</h2>
      {sub && <p className="text-muted text-xs mt-0.5">{sub}</p>}
    </div>
  )
}

function ChartTooltipCustom({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="glass-panel rounded-lg p-3 text-xs shadow-2xl border-white/5">
      <p className="text-slate-300 mb-1 font-semibold">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} style={{ color: p.color }} className="font-semibold">
          {p.name}: {typeof p.value === 'number' ? p.value.toFixed(3) : p.value}
        </p>
      ))}
    </div>
  )
}

// ── Policy editor modal ───────────────────────────────────────────────────────
function PolicyEditor({ policy, onSave, onClose }) {
  const [form, setForm] = useState({ ...policy })
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    setSaving(true)
    const params = new URLSearchParams(form).toString()
    await apiFetch(`/api/v1/portfolio/capital-model/policy?${params}`, { method: 'PUT' })
    onSave()
    setSaving(false)
    onClose()
  }

  const field = (key, label, min, max, step = 0.1) => (
    <div className="space-y-1">
      <label className="text-[10px] text-muted uppercase tracking-wide">{label}</label>
      <input
        type="number" min={min} max={max} step={step}
        value={form[key] ?? ''}
        onChange={(e) => setForm((f) => ({ ...f, [key]: parseFloat(e.target.value) }))}
        className="w-full bg-slate-800/60 border border-border rounded-lg px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
      />
    </div>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md slide-in-right" onClick={onClose}>
      <div className="glass-panel rounded-2xl p-6 w-[calc(100vw-2rem)] max-w-md shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-slate-100 font-semibold mb-4">Portfolio Policy</h3>
        <div className="grid grid-cols-2 gap-4">
          {field('max_single_stock_weight', 'Max Stock Weight (%)', 1, 50)}
          {field('max_sector_weight', 'Max Sector Weight (%)', 5, 100)}
          {field('min_cash_reserve', 'Min Cash Reserve (%)', 0, 50)}
          {field('rebalance_threshold', 'Rebalance Threshold (%)', 1, 20)}
          {field('target_annual_return', 'Target Return (%)', 5, 50)}
          {field('risk_free_rate', 'Risk-Free Rate (%, 10Y G-Sec)', 0, 20, 0.01)}
        </div>
        <div className="mt-4 space-y-1">
          <label className="text-[10px] text-muted uppercase tracking-wide">Risk Tolerance</label>
          <select
            value={form.risk_tolerance ?? 'MODERATE'}
            onChange={(e) => setForm((f) => ({ ...f, risk_tolerance: e.target.value }))}
            className="w-full bg-slate-800/60 border border-border rounded-lg px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {['CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'].map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
        <div className="flex gap-3 mt-6">
          <button onClick={onClose} className="flex-1 py-2 rounded-lg border border-border text-muted text-sm hover:text-slate-200 hover:border-slate-500 transition">
            Cancel
          </button>
          <button onClick={handleSave} disabled={saving} className="flex-1 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold transition disabled:opacity-50">
            {saving ? 'Saving…' : 'Save Policy'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Rebalance signals table ───────────────────────────────────────────────────
function RebalanceTable({ signals }) {
  if (!signals?.length) {
    return (
      <div className="glass-panel rounded-xl p-6 text-center">
        <CheckCircle size={24} className="text-emerald-400 mx-auto mb-3 drop-shadow-[0_0_10px_rgba(16,185,129,0.5)]" />
        <p className="text-slate-300 text-sm font-medium">Portfolio is balanced — no rebalance needed</p>
      </div>
    )
  }
  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="overflow-x-auto" style={{ scrollbarWidth: 'thin' }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              {['Symbol', 'Action', 'Current %', 'Target %', 'Drift', 'Reason'].map((h) => (
                <th key={h} className="text-left px-3 py-2 text-muted font-semibold uppercase tracking-wide whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {signals.map((s, i) => (
              <tr key={i} className="hover:bg-white/[0.02]">
                <td className="px-3 py-2 font-bold text-slate-100">{s.symbol?.replace('.NS', '')}</td>
                <td className="px-3 py-2">
                  <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${s.action === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
                    {s.action}
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums">{fmtPct(s.current_weight)}</td>
                <td className="px-3 py-2 tabular-nums text-indigo-400">{fmtPct(s.target_weight)}</td>
                <td className={`px-3 py-2 tabular-nums font-semibold ${s.drift > 10 ? 'text-orange-400' : 'text-slate-300'}`}>
                  {fmtPct(s.drift)}
                </td>
                <td className="px-3 py-2 text-muted max-w-xs truncate">{s.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PortfolioAnalytics() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(90)
  const [showPolicy, setShowPolicy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  async function load() {
    setRefreshing(true)
    try {
      const d = await apiFetch(`/api/v1/portfolio/capital-model?days=${days}`)
      setData(d)
    } catch (e) {
      console.error('capital-model load error', e)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => { load() }, [days])

  if (loading) return (
    <div className="flex items-center justify-center h-96">
      <LoadingSpinner />
    </div>
  )

  const m = data?.metrics ?? {}
  const policy = data?.policy ?? {}
  const posW = data?.position_weights ?? {}
  const secW = data?.sector_weights ?? {}
  const history = data?.history ?? []
  const rebalance = data?.rebalance_signals ?? []
  const snap = data?.latest_snapshot ?? {}

  // Pie data
  const posPieData = Object.entries(posW).map(([sym, w]) => ({
    name: sym.replace('.NS', ''), value: w,
  }))
  const secPieData = Object.entries(secW).map(([sec, w]) => ({
    name: sec, value: w,
  }))

  // Performance comparison chart
  const perfData = history.map((h) => ({
    date: fmtDate(h.date),
    Portfolio: h.portfolio_return != null ? Number(h.portfolio_return).toFixed(2) : null,
    Benchmark: h.benchmark_return != null ? Number(h.benchmark_return).toFixed(2) : null,
    Sharpe: h.sharpe_ratio != null ? Number(h.sharpe_ratio).toFixed(3) : null,
  }))

  // Metric quality helpers
  const sharpeColor = m.sharpe_ratio == null ? 'text-muted' : m.sharpe_ratio >= 1 ? 'text-emerald-400' : m.sharpe_ratio >= 0 ? 'text-amber-400' : 'text-red-400'
  const alphaColor = m.jensens_alpha == null ? 'text-muted' : m.jensens_alpha >= 0 ? 'text-emerald-400' : 'text-red-400'
  const retColor = m.portfolio_return == null ? 'text-muted' : m.portfolio_return >= 0 ? 'text-emerald-400' : 'text-red-400'

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-7xl mx-auto">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold tracking-tight">Portfolio Analytics</h1>
          <p className="text-muted text-xs mt-0.5">Capital model · Sharpe · Treynor · Jensen's Alpha · Rebalancing</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-slate-800/60 border border-border rounded-lg px-3 py-1.5 text-xs text-slate-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {[30, 60, 90, 180, 365].map((d) => (
              <option key={d} value={d}>{d}d look-back</option>
            ))}
          </select>
          <button
            onClick={() => setShowPolicy(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-muted text-xs hover:text-slate-200 hover:border-slate-500 transition"
          >
            <Sliders size={13} /> Policy
          </button>
          <button
            onClick={load}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/20 border border-indigo-500/30 text-indigo-300 text-xs hover:bg-indigo-600/30 transition disabled:opacity-50"
          >
            <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      </div>

      {/* Rebalance alert */}
      {snap.rebalance_needed && (
        <div className="flex items-start gap-3 rounded-xl border border-orange-500/30 bg-orange-500/10 p-4">
          <AlertTriangle size={16} className="text-orange-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-orange-200 text-sm font-semibold">Rebalancing Recommended</p>
            <p className="text-orange-300/70 text-xs mt-0.5">One or more positions have drifted beyond the rebalance threshold. Review signals below.</p>
          </div>
        </div>
      )}

      {/* Performance metrics cards */}
      <div>
        <SectionHeader title="Performance Metrics" sub={`Last ${m.days_analyzed ?? '—'} trading days · Risk-free: ${policy.risk_free_rate ?? 7.1}% (India 10Y G-Sec)`} />
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
          <StatCard
            label="Portfolio Return (ann.)"
            value={fmtPct(m.portfolio_return)}
            highlight={retColor}
            icon={TrendingUp}
            tooltip="Annualized portfolio return: mean daily return × 252"
          />
          <StatCard
            label="Benchmark (NIFTY 50)"
            value={fmtPct(m.benchmark_return)}
            icon={TrendingUp}
            tooltip="Annualized NIFTY 50 return over the same period"
          />
          <StatCard
            label="Portfolio Std Dev"
            value={fmtPct(m.portfolio_stddev)}
            icon={Activity}
            tooltip="Annualized volatility of daily returns (std dev × √252)"
          />
          <StatCard
            label="Portfolio Beta"
            value={fmtNum(m.portfolio_beta)}
            icon={Scale}
            tooltip="Weighted-average beta: Cov(Rp, Rm) / Var(Rm). β < 1 = lower systematic risk than market"
          />
          <StatCard
            label="Sharpe Ratio"
            value={fmtNum(m.sharpe_ratio)}
            highlight={sharpeColor}
            icon={Target}
            tooltip="Sharpe = (Rp − Rf) / σp. Reward-to-variability. > 1 is good, > 2 is excellent."
          />
          <StatCard
            label="Treynor Ratio"
            value={fmtNum(m.treynor_ratio)}
            icon={ArrowRightLeft}
            tooltip="Treynor = (Rp − Rf) / β. Reward-to-systematic-risk. Higher is better."
          />
          <StatCard
            label="Jensen's Alpha"
            value={m.jensens_alpha != null ? `${Number(m.jensens_alpha).toFixed(3)}%` : '—'}
            highlight={alphaColor}
            icon={TrendingUp}
            tooltip={`Jensen's α = Rp − [Rf + β(Rm − Rf)]. CAPM excess return. α > 0 means outperformance.\nCAPM expected: ${fmtPct(m.capm_expected ?? m.capm_expected_return)}`}
          />
          <StatCard
            label="Max Drawdown"
            value={m.max_drawdown != null ? `${Number(m.max_drawdown).toFixed(2)}%` : '—'}
            highlight={m.max_drawdown > 15 ? 'text-red-400' : m.max_drawdown > 7 ? 'text-amber-400' : 'text-emerald-400'}
            icon={Activity}
            tooltip="Largest peak-to-trough decline in the equity curve. Lower is safer."
          />
          <StatCard
            label="Win Rate"
            value={m.win_rate != null ? `${Number(m.win_rate).toFixed(1)}%` : '—'}
            icon={Target}
            tooltip={`Share of closed trades that were profitable (${m.closed_trades ?? 0} closed).`}
          />
          <StatCard
            label="Profit Factor"
            value={fmtNum(m.profit_factor)}
            highlight={m.profit_factor >= 1.5 ? 'text-emerald-400' : m.profit_factor >= 1 ? 'text-amber-400' : 'text-red-400'}
            icon={ArrowRightLeft}
            tooltip="Gross profit / gross loss. > 1 means net profitable; > 1.5 is healthy."
          />
        </div>

        {/* Verdict banner */}
        {m.verdict && (
          <div className={`mt-4 rounded-lg px-4 py-3 text-sm border ${
            m.verdict === 'STRONG' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
            : m.verdict === 'DECENT' ? 'border-cyan/30 bg-cyan/10 text-cyan'
            : m.verdict === 'MARGINAL' ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            : m.verdict === 'UNDERPERFORMING' ? 'border-red-500/30 bg-red-500/10 text-red-300'
            : 'border-border bg-surface/40 text-muted'
          }`}>
            <span className="font-semibold">Verdict: {m.verdict.replace(/_/g, ' ')}</span>
            {m.verdict === 'INSUFFICIENT_DATA'
              ? ` — need ≥10 closed trades for a reliable risk-adjusted read (currently ${m.closed_trades ?? 0}).`
              : ` — Sharpe ${fmtNum(m.sharpe_ratio)}, Jensen α ${m.jensens_alpha}%, beta ${fmtNum(m.portfolio_beta)}.`}
          </div>
        )}
      </div>

      {/* Charts row: historical metrics + portfolio vs benchmark */}
      {perfData.length > 1 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="glass-panel rounded-xl p-4">
            <SectionHeader title="Portfolio vs NIFTY 50" sub="Annualized return (%)" />
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={perfData}>
                <defs>
                  <linearGradient id="colorPort" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2979FF" stopOpacity={0.4}/>
                    <stop offset="95%" stopColor="#2979FF" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorBench" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00E676" stopOpacity={0.4}/>
                    <stop offset="95%" stopColor="#00E676" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_THEME.grid} />
                <XAxis dataKey="date" tick={CHART_THEME.tick} axisLine={CHART_THEME.axis} tickLine={false} />
                <YAxis tick={CHART_THEME.tick} axisLine={CHART_THEME.axis} tickLine={false} width={40} unit="%" />
                <Tooltip content={<ChartTooltipCustom />} />
                <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
                <Area type="monotone" dataKey="Portfolio" stroke="#2979FF" strokeWidth={3} fillOpacity={1} fill="url(#colorPort)" name="Portfolio" connectNulls />
                <Area type="monotone" dataKey="Benchmark" stroke="#00E676" strokeWidth={2} fillOpacity={1} fill="url(#colorBench)" name="NIFTY 50" connectNulls />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94A3B8' }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="glass-panel rounded-xl p-4">
            <SectionHeader title="Sharpe Ratio History" sub="Rolling Sharpe (annualized)" />
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={perfData}>
                <defs>
                  <linearGradient id="colorSharpe" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2979FF" stopOpacity={0.8}/>
                    <stop offset="95%" stopColor="#00B0FF" stopOpacity={0.2}/>
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_THEME.grid} />
                <XAxis dataKey="date" tick={CHART_THEME.tick} axisLine={CHART_THEME.axis} tickLine={false} />
                <YAxis tick={CHART_THEME.tick} axisLine={CHART_THEME.axis} tickLine={false} width={40} />
                <Tooltip content={<ChartTooltipCustom />} />
                <ReferenceLine y={1} stroke="#F59E0B" strokeDasharray="4 4" label={{ value: 'Good (1.0)', fill: '#F59E0B', fontSize: 9 }} />
                <Bar dataKey="Sharpe" fill="url(#colorSharpe)" radius={[4, 4, 0, 0]} name="Sharpe" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Weights charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Position weights */}
        <div className="glass-panel rounded-xl p-4">
          <SectionHeader
            title="Position Weights"
            sub={`Max per stock: ${policy.max_single_stock_weight ?? 10}%`}
          />
          {posPieData.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">No open positions</p>
          ) : (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width="50%" height={180}>
                <PieChart>
                  <Pie data={posPieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} strokeWidth={0}>
                    {posPieData.map((_, i) => <Cell key={i} fill={SECTOR_COLORS[i % SECTOR_COLORS.length]} />)}
                  </Pie>
                  <Tooltip formatter={(v) => `${v.toFixed(1)}%`} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-1.5 overflow-y-auto max-h-40">
                {posPieData.map((d, i) => (
                  <div key={d.name} className="flex items-center gap-2 text-xs">
                    <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: SECTOR_COLORS[i % SECTOR_COLORS.length] }} />
                    <span className="text-slate-300 font-medium flex-1 truncate">{d.name}</span>
                    <span className={`tabular-nums font-semibold ${d.value > (policy.max_single_stock_weight ?? 10) ? 'text-red-400' : 'text-slate-400'}`}>
                      {d.value.toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sector weights */}
        <div className="glass-panel rounded-xl p-4">
          <SectionHeader
            title="Sector Concentration"
            sub={`Max per sector: ${policy.max_sector_weight ?? 25}%`}
          />
          {secPieData.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">No sector data</p>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={secPieData} layout="vertical">
                <CartesianGrid {...CHART_THEME.grid} horizontal={false} />
                <XAxis type="number" tick={CHART_THEME.tick} axisLine={CHART_THEME.axis} tickLine={false} unit="%" />
                <YAxis type="category" dataKey="name" tick={{ ...CHART_THEME.tick, fontSize: 9 }} axisLine={false} tickLine={false} width={80} />
                <Tooltip formatter={(v) => `${v.toFixed(1)}%`} />
                <ReferenceLine x={policy.max_sector_weight ?? 25} stroke="#EF4444" strokeDasharray="3 3" />
                <Bar dataKey="value" radius={[0, 3, 3, 0]} name="Weight %">
                  {secPieData.map((d, i) => (
                    <Cell
                      key={i}
                      fill={d.value > (policy.max_sector_weight ?? 25) ? '#EF4444' : SECTOR_COLORS[i % SECTOR_COLORS.length]}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Policy summary strip */}
      <div className="glass-panel rounded-xl p-4 flex flex-wrap gap-6">
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Risk Tolerance</p>
          <p className="text-slate-100 text-sm font-semibold mt-0.5">{policy.risk_tolerance ?? '—'}</p>
        </div>
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Target Return</p>
          <p className="text-indigo-400 text-sm font-semibold mt-0.5">{fmtPct(policy.target_annual_return)} p.a.</p>
        </div>
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Max Stock Weight</p>
          <p className="text-slate-100 text-sm font-semibold mt-0.5">{fmtPct(policy.max_single_stock_weight)}</p>
        </div>
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Max Sector Weight</p>
          <p className="text-slate-100 text-sm font-semibold mt-0.5">{fmtPct(policy.max_sector_weight)}</p>
        </div>
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Min Cash Reserve</p>
          <p className="text-slate-100 text-sm font-semibold mt-0.5">{fmtPct(policy.min_cash_reserve)}</p>
        </div>
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Rebalance Threshold</p>
          <p className="text-slate-100 text-sm font-semibold mt-0.5">{fmtPct(policy.rebalance_threshold)}</p>
        </div>
        <button
          onClick={() => setShowPolicy(true)}
          className="ml-auto self-center flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-muted text-xs hover:text-slate-200 hover:border-slate-500 transition"
        >
          <Sliders size={12} /> Edit Policy
        </button>
      </div>

      {/* Rebalance signals */}
      <div>
        <SectionHeader
          title="Rebalance Signals"
          sub="Equal-weight top-10 Hub BUY universe · drift >= threshold triggers signal"
        />
        <RebalanceTable signals={rebalance} />
      </div>

      {showPolicy && (
        <PolicyEditor
          policy={policy}
          onSave={load}
          onClose={() => setShowPolicy(false)}
        />
      )}
    </div>
  )
}
