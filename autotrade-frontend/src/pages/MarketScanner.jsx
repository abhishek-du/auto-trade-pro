/**
 * MarketScanner — /discover/scanner
 *
 * Shows the current market_shortlist: every NSE stock the autonomous agent is
 * considering this cycle, ranked by master_score + volume surge.
 * The agent scans all 9,600 NSE EQ symbols every 15 min, picks the top 100,
 * and runs full deep-analysis + paper trades on them every 60 s.
 */
import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  RefreshCw, TrendingUp, TrendingDown, Minus, Zap, Filter,
  ArrowUpRight, BarChart2, Clock,
} from 'lucide-react';
import { apiFetch } from '../api/client';

const fmt = (n, d = 2) =>
  n == null || isNaN(n) ? '—'
  : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

function SignalBadge({ signal }) {
  if (!signal) return null;
  const s = signal.toUpperCase();
  const cls = s.includes('BUY')  ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/25'
            : s.includes('SELL') ? 'text-red-400 bg-red-500/10 border-red-500/25'
            :                      'text-amber-400 bg-amber-500/10 border-amber-500/25';
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${cls}`}>
      {s === 'STRONG_BUY' ? 'S.BUY' : s === 'STRONG_SELL' ? 'S.SELL' : s}
    </span>
  );
}

function ScoreBar({ score }) {
  const abs = Math.min(100, Math.abs(score || 0));
  const pos = (score || 0) >= 0;
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-white/5 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${pos ? 'bg-profit' : 'bg-loss'}`}
          style={{ width: `${abs}%` }}
        />
      </div>
      <span className={`font-mono text-[11px] ${pos ? 'text-profit' : 'text-loss'}`}>
        {pos ? '+' : ''}{fmt(score, 1)}
      </span>
    </div>
  );
}

function RsiChip({ rsi }) {
  if (rsi == null) return <span className="text-muted text-[11px]">—</span>;
  const cls = rsi > 70 ? 'text-red-400' : rsi < 30 ? 'text-profit' : 'text-slate-300';
  const label = rsi > 70 ? 'OB' : rsi < 30 ? 'OS' : '';
  return (
    <span className={`font-mono text-[11px] ${cls}`}>
      {fmt(rsi, 0)}{label && <span className="text-[9px] ml-0.5">{label}</span>}
    </span>
  );
}

const SIGNAL_FILTERS = ['ALL', 'BUY', 'STRONG_BUY', 'SELL', 'HOLD'];

export default function MarketScanner() {
  const [rows,       setRows]      = useState([]);
  const [loading,    setLoading]   = useState(true);
  const [running,    setRunning]   = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [autoFloor,  setAutoFloor]  = useState(40);
  const [sigFilter,  setSigFilter] = useState('ALL');
  const [minScore,   setMinScore]  = useState(0);
  const [search,     setSearch]    = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiFetch(`/api/v1/india/market-scanner/shortlist?limit=100&min_score=${minScore}`);
      setRows(d.shortlist || []);
      if (d.last_updated) setLastUpdate(new Date(d.last_updated));
      if (d.auto_trade_threshold != null) setAutoFloor(d.auto_trade_threshold);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [minScore]);

  useEffect(() => { load(); }, [load]);

  const triggerScan = async () => {
    setRunning(true);
    try {
      await apiFetch('/api/v1/india/market-scanner/run', { method: 'POST' });
      // Wait a moment then reload
      setTimeout(load, 3000);
    } catch {
      // ignore
    } finally {
      setTimeout(() => setRunning(false), 4000);
    }
  };

  const filtered = rows.filter(r => {
    if (sigFilter !== 'ALL' && r.signal !== sigFilter) return false;
    if (search && !r.ticker?.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const buyCount  = rows.filter(r => r.signal?.includes('BUY')).length;
  const sellCount = rows.filter(r => r.signal?.includes('SELL')).length;
  const tradeableCount = rows.filter(r => r.agent_tradeable).length;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-slate-100 text-lg font-semibold flex items-center gap-2">
            <Zap size={16} className="text-cyan" />
            Market Scanner
          </h1>
          <p className="text-muted text-xs mt-0.5">
            Full NSE universe → top {rows.length} opportunities · agent auto-trades signals ≥{autoFloor}% confidence
          </p>
          {lastUpdate && (
            <p className="text-muted text-[10px] mt-0.5 flex items-center gap-1">
              <Clock size={10} />
              Last scan: {lastUpdate.toLocaleString('en-IN', { hour12: false, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
              <span className="text-muted">· refreshes every 15 min</span>
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="text-muted hover:text-slate-300 p-2 rounded-lg hover:bg-white/5 border border-border">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={triggerScan}
            disabled={running}
            className="flex items-center gap-2 bg-cyan/10 hover:bg-cyan/20 border border-cyan/30 text-cyan text-sm font-semibold px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            <Zap size={13} />
            {running ? 'Scanning…' : 'Run scan now'}
          </button>
        </div>
      </div>

      {/* Signal summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-card border border-border rounded-xl p-3 text-center">
          <div className="text-2xl font-bold text-slate-100">{rows.length}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">In shortlist</div>
        </div>
        <div className="bg-profit/5 border border-profit/20 rounded-xl p-3 text-center">
          <div className="text-2xl font-bold text-profit">{buyCount}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">BUY signals</div>
        </div>
        <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-3 text-center">
          <div className="text-2xl font-bold text-red-400">{sellCount}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">SELL signals</div>
        </div>
        <div className="bg-cyan/5 border border-cyan/20 rounded-xl p-3 text-center">
          <div className="text-2xl font-bold text-cyan">{tradeableCount}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">Agent will trade</div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap items-center">
        {/* Signal filter */}
        <div className="flex gap-1 bg-card border border-border rounded-lg p-1">
          {SIGNAL_FILTERS.map(f => (
            <button
              key={f}
              onClick={() => setSigFilter(f)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                sigFilter === f ? 'bg-white/10 text-slate-100' : 'text-muted hover:text-slate-300'
              }`}
            >
              {f === 'ALL' ? 'All' : f === 'STRONG_BUY' ? 'S.Buy' : f.charAt(0) + f.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
        {/* Min score */}
        <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-1.5">
          <Filter size={12} className="text-muted" />
          <span className="text-muted text-xs">Min score</span>
          <select
            value={minScore}
            onChange={e => setMinScore(Number(e.target.value))}
            className="bg-transparent text-slate-200 text-xs focus:outline-none"
          >
            {[0, 20, 30, 40, 50, 60].map(v => (
              <option key={v} value={v} className="bg-slate-800">{v}+</option>
            ))}
          </select>
        </div>
        {/* Symbol search */}
        <input
          type="text"
          placeholder="Search symbol…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-card border border-border rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-muted focus:outline-none focus:border-cyan/50 w-36"
        />
        <span className="text-muted text-xs ml-auto">{filtered.length} results</span>
      </div>

      {/* Table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-muted text-sm">
            <RefreshCw size={20} className="animate-spin mx-auto mb-3 text-cyan/50" />
            Loading shortlist…
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-10 text-center">
            <BarChart2 size={32} className="mx-auto mb-3 text-muted/40" />
            <div className="text-muted text-sm font-medium">No results yet</div>
            <div className="text-muted text-xs mt-2 max-w-sm mx-auto">
              Click "Run scan now" to populate the shortlist.
              The scanner scores all NSE stocks using the Master Intelligence Hub
              and picks the top 100 candidates for the agent to trade.
            </div>
            <button
              onClick={triggerScan}
              disabled={running}
              className="mt-4 flex items-center gap-2 mx-auto bg-cyan/10 hover:bg-cyan/20 border border-cyan/30 text-cyan text-sm font-semibold px-4 py-2 rounded-lg transition-colors"
            >
              <Zap size={13} />
              {running ? 'Scanning…' : 'Run first scan'}
            </button>
          </div>
        ) : (
          <>
            {/* Desktop header */}
            <div className="hidden md:grid grid-cols-[40px_1fr_90px_100px_70px_70px_90px_100px] gap-3 px-4 py-2.5 text-[10px] text-muted uppercase tracking-wider border-b border-border">
              <span>#</span>
              <span>Symbol</span>
              <span>Signal</span>
              <span>Score</span>
              <span>Volume ×</span>
              <span>RSI</span>
              <span>vs EMA20</span>
              <span>Action</span>
            </div>
            {filtered.map((r, i) => (
              <div
                key={r.symbol}
                className={`grid grid-cols-1 md:grid-cols-[40px_1fr_90px_100px_70px_70px_90px_100px] gap-2 md:gap-3 px-4 py-3 items-center hover:bg-white/[0.02] transition-colors ${i > 0 ? 'border-t border-border' : ''}`}
              >
                {/* Rank */}
                <span className="text-muted text-xs font-mono hidden md:block">{r.rank}</span>

                {/* Symbol + sector */}
                <div className="flex items-center gap-2 min-w-0">
                  <div
                    className="w-8 h-8 rounded-lg grid place-items-center font-bold text-white text-xs shrink-0"
                    style={{ background: `hsl(${(r.ticker?.charCodeAt(0) || 65) * 37 % 360}, 50%, 25%)` }}
                  >
                    {r.ticker?.[0]}
                  </div>
                  <div className="min-w-0">
                    <Link
                      to={`/s/${r.ticker}`}
                      className="text-slate-200 font-semibold text-sm hover:text-cyan transition-colors flex items-center gap-1"
                    >
                      {r.ticker}
                      <ArrowUpRight size={11} className="text-muted" />
                      {r.hub_covered && (
                        <span className="text-[8px] font-bold text-violet-300 bg-violet-500/10 border border-violet-500/30 px-1 rounded leading-tight"
                          title="Deep-scored by the Hub: technical + news + fundamentals + earnings + sector + macro + options">
                          HUB&nbsp;7F
                        </span>
                      )}
                      {r.agent_tradeable && (
                        <span className="text-[8px] font-bold text-cyan bg-cyan/10 border border-cyan/30 px-1 rounded leading-tight"
                          title={`Agent will auto-trade — confidence ≥${autoFloor}%`}>
                          AUTO
                        </span>
                      )}
                    </Link>
                    {r.sector && <div className="text-muted text-[10px] truncate">{r.sector}</div>}
                  </div>
                  {/* Mobile: signal badge inline */}
                  <div className="md:hidden ml-auto"><SignalBadge signal={r.signal} /></div>
                </div>

                {/* Signal */}
                <div className="hidden md:block"><SignalBadge signal={r.signal} /></div>

                {/* Score bar */}
                <div><ScoreBar score={r.master_score} /></div>

                {/* Volume ratio */}
                <div>
                  <span className={`font-mono text-[11px] ${r.volume_ratio >= 2 ? 'text-cyan font-bold' : r.volume_ratio >= 1.5 ? 'text-profit' : 'text-muted'}`}>
                    {fmt(r.volume_ratio, 1)}×
                  </span>
                </div>

                {/* RSI */}
                <div><RsiChip rsi={r.rsi} /></div>

                {/* vs EMA20 */}
                <div>
                  {r.price_vs_ema20 != null ? (
                    <span className={`font-mono text-[11px] ${r.price_vs_ema20 >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {r.price_vs_ema20 >= 0 ? '+' : ''}{fmt(r.price_vs_ema20, 1)}%
                    </span>
                  ) : <span className="text-muted text-[11px]">—</span>}
                </div>

                {/* Action buttons */}
                <div className="flex gap-1.5">
                  <Link
                    to={`/s/${r.ticker}`}
                    className="text-[10px] font-semibold text-cyan bg-cyan/5 hover:bg-cyan/15 border border-cyan/20 px-2 py-1 rounded transition-colors"
                  >
                    View
                  </Link>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Footer */}
      <div className="text-muted text-[11px] text-center pb-2">
        Scanner refreshes every 15 min · agent trades top opportunities every 60 s ·
        <Link to="/agent-log" className="text-cyan hover:text-cyan/80 ml-1">view agent decisions →</Link>
      </div>
    </div>
  );
}
