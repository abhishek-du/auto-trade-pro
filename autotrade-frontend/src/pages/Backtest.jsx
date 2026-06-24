import { useState } from 'react';
import { PlayCircle, TrendingUp, TrendingDown, AlertTriangle, BarChart2 } from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { runBacktest } from '../api/client';

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(v, digits = 2) {
  if (v == null) return '—';
  const s = v.toFixed(digits);
  return v >= 0 ? `+${s}%` : `${s}%`;
}

function num(v, digits = 2) {
  if (v == null) return '—';
  return v.toFixed(digits);
}

function MetricCard({ label, value, sub, positive }) {
  const colour = positive == null
    ? 'text-slate-200'
    : positive ? 'text-emerald-400' : 'text-red-400';
  return (
    <div className="rounded-xl border border-border p-4" style={{ background: '#0D1829' }}>
      <p className="text-muted text-xs uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${colour}`}>{value}</p>
      {sub && <p className="text-muted text-xs mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Equity curve mini-chart ───────────────────────────────────────────────────

function EquityCurve({ data, initial }) {
  if (!data || data.length < 2) return <span className="text-muted text-xs">No data</span>;
  const pts = data.map((v, i) => ({ i, v }));
  const last = data[data.length - 1];
  const positive = last >= initial;
  const color = positive ? '#10b981' : '#ef4444';
  return (
    <ResponsiveContainer width="100%" height={60}>
      <AreaChart data={pts} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
        <defs>
          <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5}
          fill="url(#eq)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Symbol result row ─────────────────────────────────────────────────────────

function SymbolRow({ r, initial }) {
  const pos = r.total_return_pct >= 0;
  return (
    <tr className="border-b border-border hover:bg-white/3 transition-colors">
      <td className="px-4 py-3 font-mono text-sm text-slate-200">{r.symbol}</td>
      <td className="px-4 py-3 text-center text-sm tabular-nums">{r.total_trades}</td>
      <td className="px-4 py-3 text-center text-sm tabular-nums">
        <span className={r.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}>
          {r.win_rate.toFixed(1)}%
        </span>
      </td>
      <td className={`px-4 py-3 text-center text-sm font-semibold tabular-nums ${pos ? 'text-emerald-400' : 'text-red-400'}`}>
        {pct(r.total_return_pct)}
      </td>
      <td className="px-4 py-3 text-center text-sm tabular-nums text-red-400">
        -{r.max_drawdown_pct.toFixed(1)}%
      </td>
      <td className={`px-4 py-3 text-center text-sm tabular-nums ${
        r.sharpe_ratio == null ? 'text-muted' :
        r.sharpe_ratio >= 1 ? 'text-emerald-400' :
        r.sharpe_ratio >= 0 ? 'text-yellow-400' : 'text-red-400'
      }`}>
        {r.sharpe_ratio != null ? r.sharpe_ratio.toFixed(2) : '—'}
      </td>
      <td className="px-4 py-3 text-center text-sm tabular-nums text-muted">
        {r.profit_factor != null ? r.profit_factor.toFixed(2) : '—'}
      </td>
      <td className="px-4 py-3 w-28">
        <EquityCurve data={r.equity_curve} initial={initial} />
      </td>
    </tr>
  );
}

// ── Default config ────────────────────────────────────────────────────────────

const DEFAULT_CFG = {
  timeframe:        '1d',
  atr_multiplier:   2.0,
  risk_reward:      2.0,
  commission_pct:   0.001,
  slippage_pct:     0.0005,
  initial_capital:  100000,
  lookback_candles: 200,
};

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Backtest() {
  const [cfg, setCfg]       = useState(DEFAULT_CFG);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState(null);
  const [tab, setTab]       = useState('all');   // 'all' | 'best' | 'worst'

  const field = (key, type = 'number') => ({
    value: cfg[key],
    onChange: e => setCfg(c => ({
      ...c,
      [key]: type === 'number' ? parseFloat(e.target.value) : e.target.value,
    })),
    className: 'w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-cyan/50',
  });

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const { data } = await runBacktest(cfg);
      setResult(data);
      setTab('all');
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message);
    } finally {
      setLoading(false);
    }
  };

  const rows = result
    ? tab === 'best'  ? result.best_symbols
    : tab === 'worst' ? result.worst_symbols
    : result.all_results
    : [];

  return (
    <div className="space-y-6">

      {/* Disclaimer */}
      <div className="flex items-center gap-2 px-4 py-2 rounded-lg border border-warn/20"
        style={{ background: 'rgba(245,158,11,0.06)' }}>
        <AlertTriangle size={13} className="text-warn/70 shrink-0" />
        <span className="text-warn/70 text-xs font-semibold">
          Paper Trading Only — All backtest results are simulated; no real money is involved.
        </span>
      </div>

      {/* Config panel */}
      <div className="rounded-xl border border-border p-5" style={{ background: '#0D1829' }}>
        <div className="flex items-center gap-2 mb-4">
          <BarChart2 size={16} className="text-cyan" />
          <h2 className="text-slate-200 font-semibold text-sm">Backtest Configuration</h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="block text-muted text-xs mb-1">Timeframe</label>
            <select {...field('timeframe', 'string')}
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-cyan/50">
              {['1d','4h','1h','15m'].map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-muted text-xs mb-1">ATR Multiplier</label>
            <input type="number" step="0.5" min="1" max="5" {...field('atr_multiplier')} />
          </div>
          <div>
            <label className="block text-muted text-xs mb-1">Risk:Reward</label>
            <input type="number" step="0.5" min="1" max="5" {...field('risk_reward')} />
          </div>
          <div>
            <label className="block text-muted text-xs mb-1">Initial Capital (₹)</label>
            <input type="number" step="10000" min="10000" {...field('initial_capital')} />
          </div>
          <div>
            <label className="block text-muted text-xs mb-1">Commission %</label>
            <input type="number" step="0.0001" min="0" max="0.01" {...field('commission_pct')} />
          </div>
          <div>
            <label className="block text-muted text-xs mb-1">Slippage %</label>
            <input type="number" step="0.0001" min="0" max="0.01" {...field('slippage_pct')} />
          </div>
          <div>
            <label className="block text-muted text-xs mb-1">Lookback Candles</label>
            <input type="number" step="50" min="50" max="500" {...field('lookback_candles')} />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleRun}
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white transition-all disabled:opacity-50 glass-panel">
              <PlayCircle size={15} />
              {loading ? 'Running…' : 'Run Backtest'}
            </button>
          </div>
        </div>
        {loading && (
          <p className="text-muted text-xs mt-3 animate-pulse">
            Running walk-forward backtest across all NSE watchlist symbols — this may take a minute…
          </p>
        )}
        {error && (
          <p className="text-red-400 text-xs mt-3">{error}</p>
        )}
      </div>

      {/* Summary metrics */}
      {result && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <MetricCard label="Symbols Tested" value={result.symbols_tested} />
            <MetricCard label="Total Trades"   value={result.total_trades} />
            <MetricCard label="Avg Win Rate"   value={`${result.avg_win_rate.toFixed(1)}%`}
              positive={result.avg_win_rate >= 50} />
            <MetricCard label="Avg Return"     value={pct(result.avg_return_pct)}
              positive={result.avg_return_pct >= 0} />
            <MetricCard label="Avg Sharpe"     value={num(result.avg_sharpe)}
              sub={`in ${result.duration_seconds}s`}
              positive={result.avg_sharpe >= 1} />
          </div>

          {/* Results table */}
          <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0D1829' }}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-border">
              <h3 className="text-slate-200 font-semibold text-sm">Symbol Results</h3>
              <div className="flex gap-1">
                {['all','best','worst'].map(t => (
                  <button key={t} onClick={() => setTab(t)}
                    className={`px-3 py-1 rounded-md text-xs font-medium capitalize transition-colors ${
                      tab === t
                        ? 'text-white'
                        : 'text-muted hover:text-slate-200 hover:bg-white/5'
                    }`}
                    style={tab === t ? { background: 'linear-gradient(135deg,rgba(59,130,246,0.2),rgba(6,182,212,0.1))' } : {}}>
                    {t === 'all' ? `All (${result.symbols_tested})` : t === 'best' ? 'Top 5' : 'Bottom 5'}
                  </button>
                ))}
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {['Symbol','Trades','Win Rate','Return','Max DD','Sharpe','Profit Factor','Equity'].map(h => (
                      <th key={h} className="px-4 py-2.5 text-left text-muted text-xs font-medium uppercase tracking-wider">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <SymbolRow key={r.symbol} r={r} initial={cfg.initial_capital} />
                  ))}
                </tbody>
              </table>
              {rows.length === 0 && (
                <p className="text-muted text-sm text-center py-8">No results yet</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
