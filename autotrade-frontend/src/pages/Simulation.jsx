/**
 * Backend field mapping:
 *   /api/v1/simulation/performance  → PortfolioStatsOut
 *     { total_signals_generated, trades_taken, trades_rejected, win_rate,
 *       avg_pnl, roi_percent, best_trade, worst_trade,
 *       avg_confidence_on_wins, avg_confidence_on_losses }
 *
 *   /api/v1/portfolio/              → WalletSummary
 *     { balance, equity, realised_pnl, unrealised_pnl, win_rate,
 *       max_drawdown, peak_balance, roi_percent }
 *
 *   /api/v1/portfolio/snapshots     → PerformanceSnapshotOut[]
 *     { date, balance, equity, daily_pnl, ... }
 *
 *   /api/v1/simulation/analysis     → AnalysisEntryOut[]
 *     { id, timestamp, symbol, message, action, confidence,
 *       final_score, trade_taken, reject_reason }
 *
 *   /api/v1/simulation/logs         → SimulationLogOut[]
 *     { id, event_type, symbol, message, data, timestamp }
 */
import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  AreaChart, Area, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import {
  Activity, BarChart2, TrendingUp, TrendingDown,
  CheckCircle, XCircle, Zap, RefreshCw,
} from 'lucide-react';
import MetricCard          from '../components/MetricCard';
import SimulationLogViewer from '../components/SimulationLogViewer';
import GoLiveChecker       from '../components/GoLiveChecker';
import LoadingSpinner      from '../components/LoadingSpinner';
import {
  getSimulationPerformance, getSimulationLogs,
  getPortfolio, getPortfolioSnapshots, getSimulationAnalysis,
  getPortfolioPositions,
} from '../api/client';

/* ── helpers ─────────────────────────────────────────────────── */

const fmtUSD = (n) => '₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n ?? 0);
const fmtPct = (n) => `${(n ?? 0).toFixed(1)}%`;

const REJECTION_COLORS = ['#EF4444', '#F59E0B', '#6B7280', '#8B5CF6', '#1A56DB'];
const REJECTION_LABEL_MAP = {
  confidence_too_low: 'Confidence too low',
  max_positions:      'Max positions',
  rr_too_low:         'R:R too low',
  daily_limit:        'Daily limit',
  other:              'Other',
};

function gradientOffset(snapshots) {
  if (!snapshots?.length) return 0.5;
  const vals = snapshots.map((s) => s.balance ?? s.equity ?? 0);
  const max  = Math.max(...vals);
  const min  = Math.min(...vals);
  const start = 500000;
  if (max === min) return max >= start ? 1 : 0;
  return Math.min(1, Math.max(0, (max - start) / (max - min)));
}

function EquityTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const bal  = payload[0]?.value ?? 0;
  const dpnl = payload[0]?.payload?.daily_pnl ?? 0;
  return (
    <div className="bg-panel border border-border rounded-lg p-3 text-xs shadow-lg">
      <p className="text-muted mb-1">{label}</p>
      <p className="text-slate-100 font-bold">{fmtUSD(bal)}</p>
      <p className={dpnl >= 0 ? 'text-profit' : 'text-loss'}>
        Daily P&L: {dpnl >= 0 ? '+' : ''}{fmtUSD(dpnl)}
      </p>
    </div>
  );
}

function ScoreBar({ label, value, color }) {
  const pct = Math.round(Math.min(1, Math.max(0, value ?? 0)) * 100);
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="tabular-nums font-medium" style={{ color }}>{pct}%</span>
      </div>
      <div className="h-2 bg-surface rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  );
}

/* Format a log object → display string for SimulationLogViewer */
function formatLog(log) {
  if (typeof log === 'string') return log;
  const time = log.timestamp
    ? new Date(log.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '';
  const sym  = log.symbol && log.symbol !== '—' ? ` [${log.symbol}]` : '';
  return `[${time}] [${log.event_type ?? 'LOG'}]${sym} ${log.message ?? ''}`;
}

/* Derive signal analysis from analysis entries */
function deriveSignalAnalysis(entries) {
  const accepted = entries.filter((e) => e.trade_taken);
  const rejected = entries.filter((e) => !e.trade_taken);

  const avgOf = (arr, key) =>
    arr.length ? arr.reduce((s, e) => s + (e[key] ?? 0), 0) / arr.length : 0;

  const rejectionCounts = {};
  for (const e of rejected) {
    const reason = e.reject_reason ?? 'other';
    rejectionCounts[reason] = (rejectionCounts[reason] ?? 0) + 1;
  }

  return {
    accepted: {
      avg_pattern_score:   avgOf(accepted, 'final_score'),
      avg_indicator_score: avgOf(accepted, 'final_score'),
      avg_sentiment_score: avgOf(accepted, 'final_score'),
      avg_confidence:      avgOf(accepted, 'confidence'),
    },
    rejection_reasons: rejectionCounts,
  };
}

/* ── component ───────────────────────────────────────────────── */

export default function Simulation() {
  const [perf,       setPerf]       = useState(null);
  const [wallet,     setWallet]     = useState(null);
  const [snapshots,  setSnapshots]  = useState([]);
  const [logs,       setLogs]       = useState([]);
  const [analysis,   setAnalysis]   = useState([]);
  const [positions,  setPositions]  = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);

  const load = async () => {
    setLoading(true);
    await Promise.allSettled([
      getSimulationPerformance().then(setPerf).catch(() => null),
      getPortfolio().then(setWallet).catch(() => null),
      getPortfolioSnapshots().then((d) => setSnapshots(Array.isArray(d) ? d : [])).catch(() => null),
      getSimulationLogs().then((d) => setLogs(Array.isArray(d) ? d : [])).catch(() => null),
      getSimulationAnalysis().then((d) => setAnalysis(Array.isArray(d) ? d : [])).catch(() => null),
      getPortfolioPositions().then((d) => setPositions(Array.isArray(d) ? d : [])).catch(() => null),
    ]);
    setLoading(false);
    setLastRefresh(new Date());
  };

  useEffect(() => {
    load();
    // Positions move with the market — refresh every 30 s while the page is open
    const id = setInterval(() => {
      getPortfolioPositions().then((d) => setPositions(Array.isArray(d) ? d : [])).catch(() => null);
    }, 30000);
    return () => clearInterval(id);
  }, []);

  const formattedLogs   = useMemo(() => logs.map(formatLog), [logs]);
  const signalAnalysis  = useMemo(() => deriveSignalAnalysis(analysis), [analysis]);
  const offset          = useMemo(() => gradientOffset(snapshots), [snapshots]);

  const { accepted, rejection_reasons } = signalAnalysis;

  const pieData = Object.entries(rejection_reasons)
    .map(([key, count]) => ({
      name:  REJECTION_LABEL_MAP[key] ?? key,
      value: count,
    }))
    .filter((d) => d.value > 0);

  if (loading) return <LoadingSpinner message="Loading simulation analysis…" />;

  return (
    <div className="space-y-8">

      {/* Refresh bar */}
      <div className="flex items-center justify-between">
        <h2 className="text-slate-100 font-bold text-lg">Paper Trading Analysis Center</h2>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-muted text-xs tabular-nums">
              Updated {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={load}
            className="flex items-center gap-2 px-3 py-1.5 bg-panel border border-border hover:border-accent/50 text-muted hover:text-slate-300 rounded-lg text-xs transition-colors"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Section 1 — Performance Summary ── */}
      <section className="space-y-3">
        <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">Performance Summary</h3>
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          <MetricCard title="Signals Generated"  value={perf?.total_signals_generated ?? 0} format="count" subtitle="AI trade signals produced"    icon={Zap}         />
          <MetricCard title="Trades Taken"        value={perf?.trades_taken            ?? 0} format="count" subtitle="Orders executed"              icon={CheckCircle} />
          <MetricCard title="Trades Rejected"     value={perf?.trades_rejected         ?? 0} format="count" subtitle="Filtered out by risk rules"   icon={XCircle}     />
          <MetricCard title="Open Positions"      value={positions.length} format="count" subtitle="Live in open_positions" icon={Activity} />
        </div>
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          <MetricCard
            title="Win Rate"
            value={fmtPct(perf?.win_rate)}
            subtitle="% of closed trades profitable"
            trend={(perf?.win_rate ?? 0) - 50}
            icon={TrendingUp}
          />
          <MetricCard
            title="Avg P&L per Trade"
            value={perf?.avg_pnl ?? 0}
            subtitle="Mean P&L on closed trades"
            trend={perf?.avg_pnl ?? 0}
            icon={BarChart2}
          />
          <MetricCard
            title="Total ROI"
            value={fmtPct(perf?.roi_percent)}
            subtitle="Return on ₹5,00,000 starting balance"
            trend={perf?.roi_percent ?? 0}
            icon={TrendingUp}
          />
          <MetricCard
            title="Max Drawdown"
            value={fmtPct(wallet?.max_drawdown)}
            subtitle="Largest peak-to-trough decline"
            trend={-(wallet?.max_drawdown ?? 0)}
            icon={TrendingDown}
          />
        </div>
      </section>

      {/* ── Section 1b — Open Positions ── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">
            Open Positions <span className="text-slate-500 normal-case font-normal">· {positions.length} live</span>
          </h3>
          <span className="text-[10px] text-muted">SL trails by 1×ATR after Target 1 · rides to Target 2</span>
        </div>

        {positions.length === 0 ? (
          <div className="rounded-xl border border-border bg-panel px-4 py-8 text-center text-muted text-sm">
            No open positions. The agent opens trades during NSE hours when signals clear the risk gates.
          </div>
        ) : (
          <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
            {/* desktop header */}
            <div className="hidden md:grid grid-cols-[1.3fr_70px_repeat(4,1fr)_90px_110px] gap-3 px-4 py-2.5 text-[10px] text-muted uppercase tracking-wider border-b border-border">
              <span>Symbol</span><span>Side</span><span>Entry</span><span>Current</span>
              <span>Stop (trailing)</span><span>Target 2</span><span>Unreal P&L</span><span>Management</span>
            </div>
            <div className="divide-y divide-border">
              {positions.map((p) => {
                const isBuy = String(p.direction).toUpperCase() === 'BUY';
                const pnlUp = (p.unrealised_pnl ?? 0) >= 0;
                const tkr = p.symbol?.replace('.NS', '');
                return (
                  <Link key={p.id} to={`/s/${tkr}`}
                    className="grid grid-cols-2 md:grid-cols-[1.3fr_70px_repeat(4,1fr)_90px_110px] gap-2 md:gap-3 px-4 py-2.5 items-center hover:bg-white/[0.02] transition-colors">
                    <div className="min-w-0">
                      <p className="text-slate-200 text-sm font-semibold truncate">{tkr}</p>
                      <p className="text-muted text-[10px] md:hidden">{isBuy ? 'LONG' : 'SHORT'} · entry ₹{(+p.entry_price).toFixed(2)}</p>
                    </div>
                    <span className={`hidden md:inline text-[10px] font-bold px-1.5 py-0.5 rounded border ${isBuy ? 'text-profit bg-profit/10 border-profit/25' : 'text-loss bg-loss/10 border-loss/25'}`}>
                      {isBuy ? 'LONG' : 'SHORT'}
                    </span>
                    <span className="hidden md:block font-mono text-xs text-slate-300">₹{(+p.entry_price).toFixed(2)}</span>
                    <span className="hidden md:block font-mono text-xs text-slate-200">₹{(+p.current_price).toFixed(2)}</span>
                    <span className="hidden md:block font-mono text-xs text-loss">₹{(+p.stop_loss).toFixed(2)}</span>
                    <span className="hidden md:block font-mono text-xs text-profit">₹{(+(p.target_2 ?? p.take_profit)).toFixed(2)}</span>
                    <div className="text-right md:text-left">
                      <span className={`font-mono text-xs font-bold ${pnlUp ? 'text-profit' : 'text-loss'}`}>
                        {pnlUp ? '+' : ''}{(p.unrealised_pct ?? 0).toFixed(2)}%
                      </span>
                    </div>
                    <div className="hidden md:flex items-center gap-1">
                      {p.trailing ? (
                        <span className="text-[9px] font-bold text-cyan bg-cyan/10 border border-cyan/30 px-1 rounded" title="Target 1 hit — stop now trailing by 1×ATR">
                          TRAILING
                        </span>
                      ) : (
                        <span className="text-[9px] font-medium text-muted bg-white/5 border border-border px-1 rounded" title="Pre-Target 1 — fixed stop">
                          T1 PENDING
                        </span>
                      )}
                      {p.level_source && (
                        <span className="text-[9px] text-muted/70 uppercase">{p.level_source}</span>
                      )}
                    </div>
                  </Link>
                );
              })}
            </div>
          </div>
        )}
      </section>

      {/* ── Section 2 — Equity Curve ── */}
      <section className="space-y-3">
        <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">Equity Curve</h3>
        <div className="bg-panel border border-border rounded-xl p-5">
          {snapshots.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-muted text-sm">
              No equity snapshots yet — run the simulation to generate data
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={snapshots} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
                <defs>
                  <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset={offset}  stopColor="#10B981" stopOpacity={0.35} />
                    <stop offset={offset}  stopColor="#EF4444" stopOpacity={0.35} />
                    <stop offset="100%"    stopColor="#EF4444" stopOpacity={0.05} />
                  </linearGradient>
                  <linearGradient id="eqStroke" x1="0" y1="0" x2="0" y2="1">
                    <stop offset={offset} stopColor="#10B981" />
                    <stop offset={offset} stopColor="#EF4444" />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.5} />
                <XAxis dataKey="date" tick={{ fill: '#64748B', fontSize: 11 }} tickLine={false}
                  axisLine={{ stroke: '#334155' }} interval="preserveStartEnd" />
                <YAxis tick={{ fill: '#64748B', fontSize: 11 }} tickLine={false} axisLine={false}
                  tickFormatter={(v) => `₹${v.toLocaleString('en-IN')}`} width={72} />
                <Tooltip content={<EquityTooltip />} />
                <ReferenceLine y={500000} stroke="#6B7280" strokeDasharray="6 3"
                  label={{ value: 'Start ₹5L', fill: '#6B7280', fontSize: 11, position: 'insideTopRight' }} />
                <Area type="monotone" dataKey="balance" stroke="url(#eqStroke)" strokeWidth={2}
                  fill="url(#eqGrad)" dot={false} activeDot={{ r: 4, fill: '#F1F5F9' }} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </section>

      {/* ── Section 3 — Signal Analysis ── */}
      <section className="space-y-3">
        <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">Signal Analysis</h3>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">

          {/* Accepted */}
          <div className="bg-panel border border-border rounded-xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <CheckCircle size={16} className="text-profit" />
              <h4 className="text-slate-200 font-semibold text-sm">Why AI Accepted Trades</h4>
              <span className="text-muted text-xs ml-auto">{analysis.filter(e => e.trade_taken).length} signals</span>
            </div>
            {analysis.filter(e => e.trade_taken).length === 0 ? (
              <p className="text-muted text-xs italic">No accepted trades yet — simulation is still warming up.</p>
            ) : (
              <div className="space-y-4">
                <ScoreBar label="Pattern / Final Score"  value={accepted.avg_pattern_score}    color="#10B981" />
                <ScoreBar label="Overall Confidence"     value={accepted.avg_confidence}        color="#8B5CF6" />
                <div className="pt-2 border-t border-border text-xs text-muted space-y-1">
                  <div className="flex justify-between">
                    <span>Best trade</span>
                    <span className="text-profit font-semibold">{fmtUSD(perf?.best_trade)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Worst trade</span>
                    <span className="text-loss font-semibold">{fmtUSD(perf?.worst_trade)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Avg confidence on wins</span>
                    <span className="text-slate-300 tabular-nums">{((perf?.avg_confidence_on_wins ?? 0) * 100).toFixed(1)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Avg confidence on losses</span>
                    <span className="text-slate-300 tabular-nums">{((perf?.avg_confidence_on_losses ?? 0) * 100).toFixed(1)}%</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Rejected */}
          <div className="bg-panel border border-border rounded-xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <XCircle size={16} className="text-loss" />
              <h4 className="text-slate-200 font-semibold text-sm">Why AI Rejected Trades</h4>
              <span className="text-muted text-xs ml-auto">{analysis.filter(e => !e.trade_taken).length} signals</span>
            </div>
            {pieData.length === 0 ? (
              <p className="text-muted text-xs italic">No rejection data yet.</p>
            ) : (
              <div className="flex items-center gap-4">
                <ResponsiveContainer width={160} height={160}>
                  <PieChart>
                    <Pie data={pieData} cx={75} cy={75} innerRadius={42} outerRadius={70}
                      paddingAngle={3} dataKey="value">
                      {pieData.map((_, i) => (
                        <Cell key={i} fill={REJECTION_COLORS[i % REJECTION_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{ background: '#1E293B', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                      itemStyle={{ color: '#F1F5F9' }}
                    />
                  </PieChart>
                </ResponsiveContainer>
                <div className="space-y-2 flex-1 min-w-0">
                  {pieData.map((d, i) => (
                    <div key={i} className="flex items-center justify-between gap-2 text-xs">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="w-2.5 h-2.5 rounded-sm shrink-0"
                          style={{ backgroundColor: REJECTION_COLORS[i % REJECTION_COLORS.length] }} />
                        <span className="text-slate-400 truncate">{d.name}</span>
                      </div>
                      <span className="text-slate-300 font-semibold tabular-nums shrink-0">{d.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── Section 4 — AI Decision Log ── */}
      <section className="space-y-3">
        <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">AI Decision Log</h3>
        <SimulationLogViewer logs={formattedLogs} />
      </section>

      {/* ── Section 5 — Go-Live Checker ── */}
      <section className="space-y-3">
        <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">Go-Live Readiness</h3>
        <div className="bg-panel border border-border rounded-xl p-5">
          <GoLiveChecker />
        </div>
      </section>

    </div>
  );
}
