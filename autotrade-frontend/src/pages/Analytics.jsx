import { useState, useEffect, useMemo } from 'react';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Legend,
} from 'recharts';
import { TrendingUp, BarChart2, PieChart as PieIcon, Activity } from 'lucide-react';
import MetricCard    from '../components/MetricCard';
import LoadingSpinner from '../components/LoadingSpinner';
import { getAnalytics } from '../api/client';

const fmtINR   = (n) => `₹${Number(n ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
const fmtPct   = (n) => `${(n ?? 0).toFixed(1)}%`;
const CHART_THEME = {
  cartesian: { strokeDasharray: '3 3', stroke: '#334155', strokeOpacity: 0.6 },
  tick:      { fill: '#64748B', fontSize: 11 },
  axis:      { stroke: '#334155' },
};

function ChartTooltip({ active, payload, label, prefix = '' }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-panel border border-border rounded-lg p-3 text-xs shadow-lg">
      <p className="text-muted mb-1">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} style={{ color: p.color }} className="font-semibold">
          {p.name}: {prefix}{typeof p.value === 'number' ? p.value.toFixed(2) : p.value}
        </p>
      ))}
    </div>
  );
}

function equityOffset(data) {
  if (!data?.length) return 0.5;
  const vals  = data.map((d) => d.balance ?? d.equity ?? 0);
  const max   = Math.max(...vals), min = Math.min(...vals);
  const start = 500000;
  if (max === min) return max >= start ? 1 : 0;
  return Math.min(1, Math.max(0, (max - start) / (max - min)));
}

const WIN_LOSS_COLORS = ['#10B981', '#EF4444', '#F59E0B'];

export default function Analytics() {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAnalytics()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  const equity  = data?.equity_curve ?? [];
  const bySymbol = data?.pnl_by_symbol ?? [];
  const offset   = useMemo(() => equityOffset(equity), [equity]);

  // backend AnalyticsOut: { win_rate, avg_rr, total_trades, total_pnl,
  //   equity_curve[{date,equity}], pnl_by_symbol, trades_by_direction,
  //   daily_pnl_chart, best_trade, worst_trade, avg_trade_duration_hours }
  const totalTrades = data?.total_trades ?? 0;
  const wins   = Math.round((data?.win_rate ?? 0) / 100 * totalTrades);
  const losses = totalTrades - wins;
  const winLossPie = [
    { name: 'Wins',   value: wins },
    { name: 'Losses', value: Math.max(0, losses) },
  ].filter((d) => d.value > 0);

  const bestSymbol = bySymbol[0]?.symbol ?? '—';

  if (loading) return <LoadingSpinner message="Loading analytics…" />;

  return (
    <div className="space-y-6">

      {/* KPI row — using actual AnalyticsOut fields */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <MetricCard title="Total Trades"  value={totalTrades} subtitle="All-time completed trades" icon={Activity} format="count" />
        <MetricCard title="Total P&L"     value={data?.total_pnl ?? 0} subtitle="Sum of all closed trade P&L"
          trend={data?.roi_pct ?? null} icon={TrendingUp} />
        <MetricCard title="Avg R:R"       value={data?.avg_rr != null ? data.avg_rr.toFixed(2) : '—'}
          subtitle="Mean reward-to-risk ratio" icon={BarChart2} />
        <MetricCard title="Best Symbol"   value={bestSymbol} subtitle="Highest cumulative P&L" icon={PieIcon} />
      </div>

      {/* Equity curve */}
      <div className="bg-panel border border-border rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-2">
          <TrendingUp size={16} className="text-accent" />
          <h3 className="text-slate-200 font-semibold text-sm">Equity Curve</h3>
        </div>
        {equity.length === 0 ? (
          <div className="h-56 flex items-center justify-center text-muted text-sm">No equity data</div>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={equity} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
              <defs>
                <linearGradient id="aGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={offset}  stopColor="#10B981" stopOpacity={0.3} />
                  <stop offset={offset}  stopColor="#EF4444" stopOpacity={0.3} />
                  <stop offset="100%"    stopColor="#EF4444" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="sGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={offset} stopColor="#10B981" />
                  <stop offset={offset} stopColor="#EF4444" />
                </linearGradient>
              </defs>
              <CartesianGrid {...CHART_THEME.cartesian} />
              <XAxis dataKey="date"  tick={CHART_THEME.tick} tickLine={false} axisLine={CHART_THEME.axis} interval="preserveStartEnd" />
              <YAxis tick={CHART_THEME.tick} tickLine={false} axisLine={false} tickFormatter={fmtINR} width={68} />
              <Tooltip content={<ChartTooltip prefix="₹" />} />
              <ReferenceLine y={500000} stroke="#6B7280" strokeDasharray="5 3"
                label={{ value: '₹5L start', fill: '#6B7280', fontSize: 11, position: 'insideTopRight' }} />
              {/* backend equity_curve uses 'equity' key; daily_pnl_chart uses 'balance' */}
              <Area type="monotone" dataKey="equity" name="Balance"
                stroke="url(#sGrad)" strokeWidth={2} fill="url(#aGrad)" dot={false}
                activeDot={{ r: 4, fill: '#F1F5F9' }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* P&L by symbol + Win/Loss breakdown */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">

        {/* Bar chart — P&L by symbol */}
        <div className="xl:col-span-2 bg-panel border border-border rounded-xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <BarChart2 size={16} className="text-accent" />
            <h3 className="text-slate-200 font-semibold text-sm">P&L by Symbol</h3>
            <span className="text-muted text-xs">(top 10)</span>
          </div>
          {bySymbol.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-muted text-sm">No symbol data</div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={bySymbol.slice(0, 10)} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
                <CartesianGrid {...CHART_THEME.cartesian} vertical={false} />
                <XAxis dataKey="symbol" tick={CHART_THEME.tick} tickLine={false} axisLine={CHART_THEME.axis} />
                <YAxis tick={CHART_THEME.tick} tickLine={false} axisLine={false} tickFormatter={fmtINR} width={64} />
                <Tooltip content={<ChartTooltip prefix="₹" />} />
                <ReferenceLine y={0} stroke="#334155" />
                <Bar dataKey="pnl" name="P&L" radius={[4, 4, 0, 0]}>
                  {bySymbol.slice(0, 10).map((entry, i) => (
                    <Cell key={i} fill={(entry.pnl ?? 0) >= 0 ? '#10B981' : '#EF4444'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Pie chart — Win/Loss */}
        <div className="bg-panel border border-border rounded-xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <PieIcon size={16} className="text-accent" />
            <h3 className="text-slate-200 font-semibold text-sm">Win / Loss Breakdown</h3>
          </div>
          {winLossPie.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-muted text-sm">No data</div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={180}>
                <PieChart>
                  <Pie data={winLossPie} cx="50%" cy="50%" innerRadius={45} outerRadius={75} paddingAngle={3} dataKey="value">
                    {winLossPie.map((_, i) => <Cell key={i} fill={WIN_LOSS_COLORS[i]} />)}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: '#1E293B', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                    itemStyle={{ color: '#F1F5F9' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-2">
                {winLossPie.map((d, i) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: WIN_LOSS_COLORS[i] }} />
                      <span className="text-slate-400">{d.name}</span>
                    </div>
                    <span className="text-slate-300 font-semibold tabular-nums">{d.value}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
