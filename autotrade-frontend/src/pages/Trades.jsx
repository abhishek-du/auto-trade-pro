import { useState, useMemo } from 'react';
import { Search, ChevronLeft, ChevronRight, TrendingUp, TrendingDown, DollarSign, Activity } from 'lucide-react';
import { useTrades } from '../hooks/useTrades';
import MetricCard   from '../components/MetricCard';
import LoadingSpinner from '../components/LoadingSpinner';

const PAGE_SIZE = 20;

const fmtUSD = (n) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(n ?? 0);
const fmtDate = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }); }
  catch { return s; }
};

function DirectionBadge({ direction }) {
  const isBuy = direction?.toUpperCase() === 'BUY';
  return (
    <span className={[
      'inline-flex items-center px-2 py-0.5 rounded text-xs font-bold',
      isBuy ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss',
    ].join(' ')}>
      {isBuy ? '▲ BUY' : '▼ SELL'}
    </span>
  );
}

function PnLCell({ value }) {
  const n = Number(value ?? 0);
  return (
    <span className={`tabular-nums font-semibold text-sm ${n >= 0 ? 'text-profit' : 'text-loss'}`}>
      {n >= 0 ? '+' : ''}{fmtUSD(n)}
    </span>
  );
}

export default function Trades() {
  const { trades, loading } = useTrades();

  const [search,    setSearch]    = useState('');
  const [direction, setDirection] = useState('All');
  const [status,    setStatus]    = useState('All');
  const [page,      setPage]      = useState(1);

  const filtered = useMemo(() => {
    return trades.filter((t) => {
      const sym = (t.symbol ?? t.ticker ?? '').toUpperCase();
      if (search    && !sym.includes(search.toUpperCase())) return false;
      if (direction !== 'All' && (t.direction ?? t.side ?? '').toUpperCase() !== direction) return false;
      if (status    !== 'All' && (t.status ?? 'CLOSED').toUpperCase() !== status) return false;
      return true;
    });
  }, [trades, search, direction, status]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const pageRows   = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  /* ── summary stats ── */
  const closed    = trades.filter((t) => (t.status ?? 'CLOSED').toUpperCase() === 'CLOSED');
  const wins      = closed.filter((t) => (t.pnl ?? 0) > 0);
  const totalPnl  = closed.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const winRate   = closed.length ? (wins.length / closed.length) * 100 : 0;
  const bestTrade = closed.reduce((b, t) => Math.max(b, t.pnl ?? 0), 0);
  const worstTrade = closed.reduce((b, t) => Math.min(b, t.pnl ?? 0), 0);

  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-6">

      {/* Summary cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <MetricCard title="Total Trades"    value={trades.length}      subtitle="All time"                      icon={Activity}    />
        <MetricCard title="Win Rate"        value={`${winRate.toFixed(1)}%`} subtitle="Closed profitable trades" trend={winRate - 50} icon={TrendingUp} />
        <MetricCard title="Total P&L"       value={totalPnl}           subtitle="Sum of all closed trade P&L"   trend={totalPnl > 0 ? 1 : -1} icon={DollarSign} />
        <MetricCard title="Best / Worst"    value={fmtUSD(bestTrade)}  subtitle={`Worst: ${fmtUSD(worstTrade)}`} icon={TrendingDown} />
      </div>

      {/* Filters */}
      <div className="bg-panel border border-border rounded-xl p-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-40">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder="Search symbol…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="w-full bg-surface border border-border rounded-lg pl-8 pr-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>
        {[
          { label: 'Direction', value: direction, set: setDirection, opts: ['All', 'BUY', 'SELL'] },
          { label: 'Status',    value: status,    set: setStatus,    opts: ['All', 'OPEN', 'CLOSED'] },
        ].map(({ label, value, set, opts }) => (
          <div key={label} className="flex items-center gap-2">
            <span className="text-muted text-xs">{label}:</span>
            <div className="flex rounded-lg overflow-hidden border border-border">
              {opts.map((o) => (
                <button
                  key={o}
                  onClick={() => { set(o); setPage(1); }}
                  className={[
                    'px-3 py-2 text-xs font-medium transition-colors',
                    value === o ? 'bg-accent text-white' : 'text-muted hover:text-slate-300 hover:bg-surface',
                  ].join(' ')}
                >
                  {o}
                </button>
              ))}
            </div>
          </div>
        ))}
        <span className="text-muted text-xs ml-auto">{filtered.length} trades</span>
      </div>

      {/* Table */}
      <div className="bg-panel border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Date', 'Symbol', 'Direction', 'Entry', 'Exit', 'Qty', 'P&L', 'P&L %', 'Status'].map((h) => (
                  <th key={h} className="text-left px-4 py-3 text-muted text-xs font-semibold uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="text-center py-12 text-muted text-sm">
                    No trades match the current filters.
                  </td>
                </tr>
              ) : (
                pageRows.map((t, i) => {
                  const pnl    = t.pnl ?? 0;
                  const pnlPct = t.pnl_pct ?? 0;
                  return (
                    <tr key={t.id ?? i} className="border-b border-border/50 hover:bg-surface/50 transition-colors">
                      <td className="px-4 py-3 text-muted text-xs tabular-nums whitespace-nowrap">{fmtDate(t.closed_at ?? t.opened_at)}</td>
                      <td className="px-4 py-3 text-slate-200 font-medium">{t.symbol ?? t.ticker ?? '—'}</td>
                      <td className="px-4 py-3"><DirectionBadge direction={t.direction ?? t.side} /></td>
                      <td className="px-4 py-3 text-slate-300 tabular-nums">{fmtUSD(t.entry_price)}</td>
                      <td className="px-4 py-3 text-slate-300 tabular-nums">{t.exit_price ? fmtUSD(t.exit_price) : '—'}</td>
                      <td className="px-4 py-3 text-slate-300 tabular-nums">{t.quantity ?? '—'}</td>
                      <td className="px-4 py-3"><PnLCell value={pnl} /></td>
                      <td className={`px-4 py-3 tabular-nums text-sm font-semibold ${pnlPct >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                      </td>
                      <td className="px-4 py-3">
                        <span className={[
                          'text-xs font-medium px-2 py-0.5 rounded',
                          (t.status ?? 'CLOSED').toUpperCase() === 'OPEN'
                            ? 'bg-accent/20 text-accent'
                            : 'bg-surface text-muted',
                        ].join(' ')}>
                          {t.status ?? 'CLOSED'}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border">
            <span className="text-muted text-xs">
              Page {safePage} of {totalPages} · {filtered.length} trades
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage === 1}
                className="p-1.5 rounded hover:bg-surface text-muted disabled:opacity-30 transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const start = Math.max(1, Math.min(safePage - 2, totalPages - 4));
                const n = start + i;
                return (
                  <button
                    key={n}
                    onClick={() => setPage(n)}
                    className={[
                      'w-8 h-8 rounded text-xs font-medium transition-colors',
                      n === safePage ? 'bg-accent text-white' : 'text-muted hover:bg-surface hover:text-slate-300',
                    ].join(' ')}
                  >
                    {n}
                  </button>
                );
              })}
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage === totalPages}
                className="p-1.5 rounded hover:bg-surface text-muted disabled:opacity-30 transition-colors"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
