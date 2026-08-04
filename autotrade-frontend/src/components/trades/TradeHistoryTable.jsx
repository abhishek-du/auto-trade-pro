import { useMemo, useState, Fragment } from 'react';
import {
  useReactTable, createColumnHelper, getCoreRowModel,
  getSortedRowModel, getPaginationRowModel, flexRender,
} from '@tanstack/react-table';
import {
  ChevronLeft, ChevronRight, ChevronDown, ChevronUp, ChevronsUpDown,
  Zap, Sparkles, Bot,
} from 'lucide-react';
import DirectionBadge from './DirectionBadge';
import PnLPct from './PnLPct';
import TradeDetailPanel from './TradeDetailPanel';
import TradeHistoryFilters from './TradeHistoryFilters';
import { fmt, fmtQty } from '../../utils/tradeFormat';
import { asUTCDate } from '../../utils/datetime';
import { exportToCsv } from '../../utils/csvExport';

const PAGE_SIZE = 20;

const fmtTimeIST = (s) => {
  const d = asUTCDate(s);
  if (!d || isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: true });
};
const fmtDateShort = (s) => {
  const d = asUTCDate(s);
  if (!d || isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short' });
};

const columnHelper = createColumnHelper();

function SortIcon({ dir }) {
  if (dir === 'asc') return <ChevronUp size={12} aria-hidden="true" />;
  if (dir === 'desc') return <ChevronDown size={12} aria-hidden="true" />;
  return <ChevronsUpDown size={11} className="opacity-40" aria-hidden="true" />;
}

/**
 * Trade history table -- react-table for sorting/pagination (matching the
 * PositionsTable idiom from Phase 3, including the same columnHelper `id`
 * gotcha: every accessor below sets `id` explicitly since several use
 * accessorFn rather than a plain key). Row-expansion (the rich
 * TradeDetailPanel) is handled with plain local state rather than
 * react-table's expanding API -- the detail row is a second, differently-
 * shaped <tr> injected via Fragment, same structure the original inline
 * table used, which react-table's expanding model doesn't fit any more
 * cleanly than this does.
 */
export default function TradeHistoryTable({ trades, positionBySymbol, loading, error }) {
  const [search, setSearch] = useState('');
  const [direction, setDirection] = useState('All');
  const [status, setStatus] = useState('All');
  const [source, setSource] = useState('All');
  const [compareOpen, setCompareOpen] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [sorting, setSorting] = useState([{ id: 'time', desc: true }]);
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: PAGE_SIZE });

  const enriched = useMemo(() => (trades || []).map((t, i) => {
    const isOpen = (t.status ?? 'CLOSED').toUpperCase() === 'OPEN';
    const tradeSym = (t.symbol ?? t.ticker ?? '').replace('.NS', '').toUpperCase();
    const pos = isOpen ? (positionBySymbol[tradeSym] ?? null) : null;
    const pnl = isOpen ? (pos?.unrealised_pnl ?? t.unrealised_pnl ?? t.pnl ?? 0) : (t.pnl ?? 0);
    const pnlPct = isOpen ? (pos?.unrealised_pct ?? t.unrealised_pct ?? t.pnl_percent ?? t.pnl_pct ?? 0) : (t.pnl_percent ?? t.pnl_pct ?? 0);
    const invested = t.size_usd ?? 0;
    const curPrice = isOpen ? (pos?.current_price ?? t.current_price ?? null) : (t.exit_price ?? null);
    const tradeIsBuy = (t.direction ?? t.side ?? '').toUpperCase() === 'BUY';
    const curVal = invested + (tradeIsBuy ? pnl : -pnl);
    const alsoOpenNow = tradeSym in positionBySymbol;
    return {
      rowId: t.id ?? `row_${i}`,
      raw: t, isOpen, tradeSym, pos, pnl, pnlPct, invested, curPrice, curVal,
      isGain: pnl >= 0, alsoOpenNow,
    };
  }), [trades, positionBySymbol]);

  const filtered = useMemo(() => enriched.filter((r) => {
    const sym = (r.raw.symbol ?? r.raw.ticker ?? '').toUpperCase();
    if (search && !sym.includes(search.toUpperCase())) return false;
    if (direction !== 'All' && (r.raw.direction ?? r.raw.side ?? '').toUpperCase() !== direction) return false;
    if (status !== 'All' && (r.raw.status ?? 'CLOSED').toUpperCase() !== status) return false;
    if (source !== 'All' && (r.raw.strategy_source ?? 'Unknown') !== source) return false;
    if (compareOpen && !r.alsoOpenNow) return false;
    return true;
  }), [enriched, search, direction, status, source, compareOpen]);

  const columns = useMemo(() => [
    columnHelper.accessor((r) => new Date(r.raw.opened_at ?? 0).getTime(), {
      id: 'time',
      header: 'Time (IST)',
      cell: ({ row }) => {
        const t = row.original.raw;
        const isBuyDir = (t.direction ?? '').toUpperCase() === 'BUY';
        const isOpen = row.original.isOpen;
        return (
          <div>
            <div className="text-[10px] text-muted mb-1 font-medium">{fmtDateShort(t.opened_at)}</div>
            <div className="flex items-center gap-1">
              <span className={`text-[9px] ${isBuyDir ? 'text-emerald-400' : 'text-rose-400'}`}>{isBuyDir ? '▲' : '▼'}</span>
              <span className={`text-[11px] tabular-nums font-semibold ${isBuyDir ? 'text-emerald-400' : 'text-rose-400'}`}>{fmtTimeIST(t.opened_at)}</span>
              <span className="text-[9px] text-muted">{isBuyDir ? 'buy' : 'sell'}</span>
            </div>
            {!isOpen && t.closed_at ? (
              <div className="flex items-center gap-1 mt-0.5">
                <span className={`text-[9px] ${isBuyDir ? 'text-rose-400' : 'text-emerald-400'}`}>{isBuyDir ? '▼' : '▲'}</span>
                <span className={`text-[11px] tabular-nums font-semibold ${isBuyDir ? 'text-rose-400' : 'text-emerald-400'}`}>{fmtTimeIST(t.closed_at)}</span>
                <span className="text-[9px] text-muted">{isBuyDir ? 'sell' : 'cover'}</span>
              </div>
            ) : isOpen ? (
              <div className="flex items-center gap-1 mt-0.5">
                <span className="w-1 h-1 rounded-full bg-profit animate-pulse" />
                <span className="text-[10px] text-profit">open</span>
              </div>
            ) : null}
          </div>
        );
      },
    }),
    columnHelper.accessor((r) => r.raw.symbol ?? r.raw.ticker ?? '', {
      id: 'symbol',
      header: 'Symbol',
      cell: ({ row }) => {
        const t = row.original.raw;
        const isOpen = row.original.isOpen;
        return (
          <div className="flex items-center gap-1.5">
            {isOpen && <Zap size={11} className="text-profit shrink-0" aria-hidden="true" />}
            {(t.option_type === 'CE' || t.option_type === 'PE') ? (
              <div className="flex flex-col">
                <span className="text-slate-200 font-medium">
                  {t.underlying_symbol} {t.strike_price != null ? Number(t.strike_price).toFixed(0) : ''}{' '}
                  <span className={t.option_type === 'CE' ? 'text-profit' : 'text-loss'}>{t.option_type}</span>
                </span>
                <span className="text-[10px] text-muted">Exp {t.expiry_date?.slice(0, 10) ?? '—'} · option premium</span>
              </div>
            ) : t.instrument_type === 'FUTURE' ? (
              <div className="flex flex-col">
                <span className="text-slate-200 font-medium">{t.underlying_symbol} <span className="text-blue-300">FUT</span></span>
                <span className="text-[10px] text-muted">Exp {t.expiry_date?.slice(0, 10) ?? '—'} · index level</span>
              </div>
            ) : (
              <span className="text-slate-200 font-medium">{t.symbol ?? t.ticker ?? '—'}</span>
            )}
            {compareOpen && row.original.alsoOpenNow && !isOpen && (
              <span
                title="This symbol also has an open position right now"
                className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-cyan/10 text-cyan border border-cyan/25"
              >
                also open
              </span>
            )}
          </div>
        );
      },
    }),
    columnHelper.accessor((r) => r.raw.strategy_source ?? 'Unknown', {
      id: 'source',
      header: 'Source',
      cell: ({ getValue }) => {
        const src = getValue();
        if (src === 'AI Predict') {
          return (
            <span title="Opened by the Pre-Event Expectation Gap strategy (source: AI Predict)" className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
              <Sparkles size={9} /> AI Predict
            </span>
          );
        }
        if (src === 'Direct News') {
          return (
            <span title="Opened by the Direct News strategy — trades directly off classified sentiment/materiality, no LLM debate (source: Direct News)" className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30">
              <Zap size={9} /> Direct News
            </span>
          );
        }
        return (
          <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded bg-violet-500/20 text-violet-300 border border-violet-500/30">
            <Bot size={9} /> AI
          </span>
        );
      },
    }),
    columnHelper.accessor((r) => r.raw.direction ?? r.raw.side ?? '', {
      id: 'direction',
      header: 'Direction',
      cell: ({ row }) => <DirectionBadge direction={row.original.raw.direction ?? row.original.raw.side} />,
    }),
    columnHelper.accessor((r) => r.raw.size_units ?? 0, {
      id: 'qty',
      header: 'Qty / Invested',
      cell: ({ row }) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-slate-200 tabular-nums font-semibold">
            {fmtQty(row.original.raw.size_units)} <span className="text-muted font-normal text-[11px]">shares</span>
          </span>
          <span className="text-muted text-[10px] tabular-nums">{fmt(row.original.invested)}</span>
        </div>
      ),
    }),
    columnHelper.accessor((r) => r.raw.entry_price ?? 0, {
      id: 'entry',
      header: 'Entry',
      cell: ({ getValue }) => <span className="text-slate-300 tabular-nums">{fmt(getValue())}</span>,
    }),
    columnHelper.accessor((r) => r.curPrice ?? 0, {
      id: 'current',
      header: 'Current / Exit',
      cell: ({ row }) => {
        const { curPrice, isOpen, isGain, raw } = row.original;
        if (isOpen && curPrice) {
          return (
            <div className="flex flex-col gap-0.5">
              <span className={`tabular-nums font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>{fmt(curPrice)}</span>
              <span className="text-[10px] text-muted">{curPrice >= raw.entry_price ? '▲' : '▼'} {fmt(Math.abs(curPrice - raw.entry_price))}</span>
            </div>
          );
        }
        if (curPrice) return <span className="text-slate-300 tabular-nums">{fmt(curPrice)}</span>;
        return <span className="text-muted text-xs">—</span>;
      },
    }),
    columnHelper.accessor((r) => r.curVal, {
      id: 'value',
      header: 'Current Value',
      cell: ({ row }) => {
        const { curVal, isGain, pnl } = row.original;
        return (
          <div className="flex flex-col gap-0.5">
            <span className={`tabular-nums font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>{fmt(curVal)}</span>
            <span className="text-muted text-[10px]">{isGain ? '▲' : '▼'} {fmt(Math.abs(pnl))}</span>
          </div>
        );
      },
    }),
    columnHelper.accessor((r) => r.pnl, {
      id: 'pnl',
      header: 'P&L',
      cell: ({ row }) => {
        const { pnl, isGain } = row.original;
        return <span className={`tabular-nums font-semibold text-sm ${isGain ? 'text-profit' : 'text-loss'}`}>{isGain ? '+' : ''}{fmt(pnl)}</span>;
      },
    }),
    columnHelper.accessor((r) => r.pnlPct, {
      id: 'pnlPct',
      header: 'P&L %',
      cell: ({ getValue }) => <PnLPct value={getValue()} />,
    }),
    columnHelper.accessor((r) => (r.isOpen ? 'LIVE' : (r.raw.status ?? 'CLOSED')), {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => {
        const { isOpen, raw } = row.original;
        return (
          <span className={['text-xs font-medium px-2 py-0.5 rounded', isOpen ? 'bg-profit/20 text-profit animate-pulse' : 'bg-surface text-muted'].join(' ')}>
            {isOpen ? 'LIVE' : (raw.status ?? 'CLOSED')}
          </span>
        );
      },
    }),
  ], [compareOpen]);

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting, pagination },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    getRowId: (row) => String(row.rowId),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  function handleExport() {
    const headers = ['Time', 'Symbol', 'Source', 'Direction', 'Qty', 'Invested', 'Entry', 'Current/Exit', 'Current Value', 'P&L', 'P&L %', 'Status'];
    const rows = table.getSortedRowModel().rows.map(({ original: r }) => [
      r.raw.opened_at ?? '', (r.raw.symbol ?? r.raw.ticker ?? '').replace('.NS', ''),
      r.raw.strategy_source ?? 'Unknown', r.raw.direction ?? r.raw.side ?? '',
      r.raw.size_units ?? '', r.invested, r.raw.entry_price ?? '', r.curPrice ?? '',
      r.curVal, r.pnl, r.pnlPct, r.isOpen ? 'LIVE' : (r.raw.status ?? 'CLOSED'),
    ]);
    exportToCsv('trade_history.csv', headers, rows);
  }

  if (error) {
    return (
      <div className="glass-panel rounded-xl p-8 text-center">
        <p className="text-loss text-sm font-medium">Couldn't load trade history.</p>
        <p className="text-muted text-xs mt-1">{error.message ?? 'Please try again shortly.'}</p>
      </div>
    );
  }

  const rows = table.getRowModel().rows;
  const pageCount = table.getPageCount();
  const pageIndex = table.getState().pagination.pageIndex;

  return (
    <div className="glass-panel rounded-xl overflow-hidden hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] transition-all duration-300 relative">
      <TradeHistoryFilters
        search={search} onSearchChange={setSearch}
        direction={direction} onDirectionChange={setDirection}
        status={status} onStatusChange={setStatus}
        source={source} onSourceChange={setSource}
        compareOpen={compareOpen} onCompareOpenChange={setCompareOpen}
        resultCount={filtered.length}
        onExport={handleExport}
      />

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {table.getFlatHeaders().map((header) => (
                <th
                  key={header.id}
                  className="text-left px-4 py-3 text-muted text-xs font-semibold uppercase tracking-wider whitespace-nowrap"
                >
                  <button
                    type="button"
                    onClick={header.column.getToggleSortingHandler()}
                    className="flex items-center gap-1 hover:text-slate-300 transition-colors"
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    <SortIcon dir={header.column.getIsSorted()} />
                  </button>
                </th>
              ))}
              <th className="px-3 py-3 w-8" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={12} className="text-center py-12 text-muted text-sm">Loading trades…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={12} className="text-center py-12 text-muted text-sm">No trades match the current filters.</td></tr>
            ) : (
              rows.map((row) => {
                const r = row.original;
                const isExpanded = expandedId === r.rowId;
                const toggleExpanded = () => setExpandedId(isExpanded ? null : r.rowId);
                const rowSymbol = r.raw.symbol ?? r.raw.ticker ?? 'trade';
                return (
                  <Fragment key={row.id}>
                    <tr
                      onClick={toggleExpanded}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleExpanded(); } }}
                      tabIndex={0}
                      role="button"
                      aria-expanded={isExpanded}
                      aria-label={`${rowSymbol} trade details, ${isExpanded ? 'expanded' : 'collapsed'}`}
                      className={`border-b cursor-pointer hover:bg-surface/50 transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan/60 focus-visible:bg-surface/50 ${
                        isExpanded ? 'border-border/20 bg-surface/30' : 'border-border/50'
                      } ${r.isOpen ? 'bg-profit/[0.03]' : ''}`}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-3 whitespace-nowrap">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                      <td className="px-3 py-3 text-right">
                        <ChevronDown size={14} className={`text-muted transition-transform duration-200 ${isExpanded ? 'rotate-180 text-cyan' : ''}`} />
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        <td colSpan={12} className="p-0">
                          <TradeDetailPanel trade={r.raw} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {filtered.length > 0 && (
        <div className="md:hidden pointer-events-none absolute top-10 bottom-0 right-0 w-8 bg-gradient-to-l from-[#0a1120] to-transparent" />
      )}

      {pageCount > 1 && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-border">
          <span className="text-muted text-xs">
            Page {pageIndex + 1} of {pageCount} · {filtered.length} trades
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="p-1.5 rounded hover:bg-surface text-muted disabled:opacity-30 transition-colors"
              aria-label="Previous page"
            >
              <ChevronLeft size={16} />
            </button>
            {Array.from({ length: Math.min(5, pageCount) }, (_, ix) => {
              const start = Math.max(0, Math.min(pageIndex - 2, pageCount - 5));
              const n = start + ix;
              return (
                <button
                  key={n}
                  type="button"
                  onClick={() => table.setPageIndex(n)}
                  aria-current={n === pageIndex}
                  className={[
                    'w-8 h-8 rounded text-xs font-medium transition-colors',
                    n === pageIndex ? 'bg-accent text-white' : 'text-muted hover:bg-surface hover:text-slate-300',
                  ].join(' ')}
                >
                  {n + 1}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="p-1.5 rounded hover:bg-surface text-muted disabled:opacity-30 transition-colors"
              aria-label="Next page"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
