import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, TrendingUp, TrendingDown, Minus, AlertCircle, CheckCircle, XCircle } from 'lucide-react';
import { apiFetch } from '../api/client';
import { fmtIST } from '../utils/datetime';

const fmt2 = (n) => n == null ? '—' : Number(n).toFixed(1);

function ConfBar({ value, max = 100 }) {
  const pct = Math.min(100, Math.max(0, Math.abs(value || 0)));
  const color = pct >= 60 ? 'bg-profit' : pct >= 40 ? 'bg-amber-400' : 'bg-slate-500';
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`font-mono text-[11px] w-8 text-right shrink-0 ${pct >= 60 ? 'text-profit' : pct >= 40 ? 'text-amber-400' : 'text-muted'}`}>
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}

function ActionChip({ action }) {
  if (!action) return <span className="text-muted text-[10px]">—</span>;
  const a = action.toUpperCase();
  const cls = a.includes('BUY') ? 'text-profit bg-profit/10 border-profit/30'
            : a.includes('SELL') ? 'text-loss bg-loss/10 border-loss/30'
            : 'text-muted bg-white/5 border-border';
  return <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${cls}`}>{a}</span>;
}

function TradeStatusIcon({ taken, reason }) {
  if (taken) return <CheckCircle size={14} className="text-profit shrink-0" />;
  if (reason?.toLowerCase().includes('confidence')) return <AlertCircle size={14} className="text-amber-400 shrink-0" />;
  return <XCircle size={14} className="text-muted shrink-0" />;
}

export default function AgentLog() {
  const [entries, setEntries]   = useState([]);
  const [loading, setLoading]   = useState(true);
  const [filter,  setFilter]    = useState('all'); // all | traded | skipped
  const [symFilter, setSymFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiFetch('/api/v1/india/agent-log?limit=200');
      setEntries(d.entries || []);
    } catch (e) {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = entries.filter(e => {
    if (filter === 'traded' && !e.trade_taken) return false;
    if (filter === 'skipped' && e.trade_taken) return false;
    if (symFilter && !e.symbol?.toLowerCase().includes(symFilter.toLowerCase())) return false;
    return true;
  });

  const traded  = entries.filter(e => e.trade_taken).length;
  const skipped = entries.filter(e => !e.trade_taken).length;

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-slate-100 text-lg font-semibold">Agent Decision Log</h1>
          <p className="text-muted text-xs mt-0.5">Every symbol the agent analysed — traded or rejected, and why</p>
        </div>
        <button onClick={load} className="text-muted hover:text-slate-300 p-2 rounded-lg hover:bg-white/5">
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Summary chips */}
      <div className="flex gap-3 flex-wrap">
        <div className="bg-card border border-border rounded-xl px-4 py-3 text-center min-w-[100px]">
          <div className="text-2xl font-bold text-slate-100">{entries.length}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">Total analysed</div>
        </div>
        <div className="bg-profit/5 border border-profit/20 rounded-xl px-4 py-3 text-center min-w-[100px]">
          <div className="text-2xl font-bold text-profit">{traded}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">Trades opened</div>
        </div>
        <div className="bg-card border border-border rounded-xl px-4 py-3 text-center min-w-[100px]">
          <div className="text-2xl font-bold text-amber-400">{skipped}</div>
          <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">Skipped</div>
        </div>
        {entries.length > 0 && (
          <div className="bg-card border border-border rounded-xl px-4 py-3 text-center min-w-[120px]">
            <div className="text-2xl font-bold text-slate-100">{((traded / entries.length) * 100).toFixed(0)}%</div>
            <div className="text-muted text-[10px] uppercase tracking-wider mt-0.5">Trade rate</div>
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap items-center">
        <div className="flex gap-1 bg-card border border-border rounded-lg p-1">
          {[['all', 'All'], ['traded', 'Traded'], ['skipped', 'Skipped']].map(([v, l]) => (
            <button key={v} onClick={() => setFilter(v)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${filter === v ? 'bg-white/10 text-slate-100' : 'text-muted hover:text-slate-300'}`}>
              {l}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Filter by symbol…"
          value={symFilter}
          onChange={e => setSymFilter(e.target.value)}
          className="bg-card border border-border rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-muted focus:outline-none focus:border-cyan/50 w-44"
        />
        <span className="text-muted text-xs ml-auto">{filtered.length} entries</span>
      </div>

      {/* Log table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-muted text-sm">Loading agent decisions…</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center">
            <div className="text-muted text-sm">No agent decisions logged yet.</div>
            <div className="text-muted text-xs mt-2">
              The paper-trade loop runs every 60 s during market hours.
              Add stocks to your watchlist to see them analysed here.
            </div>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {/* Table header */}
            <div className="hidden md:grid grid-cols-[1fr_80px_80px_160px_1fr] gap-4 px-4 py-2 text-[10px] text-muted uppercase tracking-wider">
              <span>Symbol</span>
              <span>Action</span>
              <span>Confidence</span>
              <span>Status</span>
              <span>Reason / Rejection</span>
            </div>
            {filtered.map((e, i) => (
              <div key={e.id ?? i}
                className="grid grid-cols-1 md:grid-cols-[1fr_80px_80px_160px_1fr] gap-2 md:gap-4 px-4 py-3 hover:bg-white/[0.02] items-start md:items-center">
                {/* Symbol */}
                <div className="flex items-center gap-2">
                  <TradeStatusIcon taken={e.trade_taken} reason={e.reject_reason} />
                  <div>
                    <Link to={`/s/${e.symbol?.replace('.NS','')}`}
                      className="text-slate-200 text-sm font-semibold hover:text-cyan transition-colors">
                      {e.symbol?.replace('.NS', '')}
                    </Link>
                    {e.timestamp && (
                      <div className="text-muted text-[10px] font-mono">
                        {fmtIST(e.timestamp, { hour12: false, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </div>
                    )}
                  </div>
                </div>
                {/* Action */}
                <div><ActionChip action={e.action} /></div>
                {/* Confidence */}
                <div><ConfBar value={e.confidence} /></div>
                {/* Status */}
                <div>
                  {e.trade_taken ? (
                    <span className="text-profit text-xs font-semibold">✓ Trade opened</span>
                  ) : (
                    <span className="text-muted text-xs">Skipped</span>
                  )}
                  {e.final_score != null && (
                    <span className={`ml-2 font-mono text-[10px] ${e.final_score >= 0 ? 'text-profit' : 'text-loss'}`}>
                      score {e.final_score >= 0 ? '+' : ''}{fmt2(e.final_score)}
                    </span>
                  )}
                </div>
                {/* Reason */}
                <div className="text-xs text-muted leading-snug">
                  {e.trade_taken
                    ? (e.reasoning?.[0] || 'Signal passed all checks')
                    : (e.reject_reason || '—')}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-muted text-[11px] text-center pb-4">
        Agent runs every 60 s · paper trades only · ₹5,00,000 virtual capital · confidence gate: 40%
      </p>
    </div>
  );
}
