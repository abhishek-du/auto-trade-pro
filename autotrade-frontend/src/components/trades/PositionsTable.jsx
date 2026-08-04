import { useMemo, useRef } from 'react';
import {
  useReactTable, getCoreRowModel, getSortedRowModel, flexRender, createColumnHelper,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';
import DirectionBadge from './DirectionBadge';
import PnLPct from './PnLPct';
import ColumnManagerPopover from './ColumnManagerPopover';
import { POSITION_COLUMNS } from '../../utils/positionColumns';
import { fmt, fmtQty, elapsed } from '../../utils/tradeFormat';
import { useTradesPreferences } from '../../contexts/TradesPreferencesContext';

const columnHelper = createColumnHelper();

// Fixed pixel widths so the header (real DOM) and virtualized body (grid-
// templated divs, not a real <table> -- absolute-positioned virtual rows
// don't work inside <tbody>/<tr>, the standard workaround when combining
// react-table with react-virtual) line up exactly.
const COLUMN_WIDTH = {
  symbol: 110, source: 110, direction: 90, qty: 90, entry: 100, current: 100,
  value: 110, pnl: 110, pnlPct: 90, slTpDistance: 170, holdingTime: 90,
};

function deriveRow(pos) {
  const pnl = pos.unrealised_pnl ?? 0;
  const isBuy = pos.direction?.toUpperCase() === 'BUY';
  const currentVal = (pos.size_usd ?? 0) + (isBuy ? pnl : -pnl);
  const slDist = pos.stop_loss
    ? Math.abs((pos.current_price - pos.stop_loss) / pos.current_price * 100)
    : null;
  const tpDist = pos.take_profit
    ? Math.abs((pos.take_profit - pos.current_price) / pos.current_price * 100)
    : null;
  return { ...pos, currentVal, slDist, tpDist };
}

// Every accessor column below is given an explicit `id` matching
// utils/positionColumns.js's catalog ids -- columnHelper.accessor() defaults
// a column's internal id to the accessor KEY (e.g. 'strategySource',
// 'size_units'), not the catalog id ('source', 'qty'). Without the explicit
// id, the `columnOrder` table state (sourced from those catalog ids) can't
// match most columns, silently falling back to an unpredictable order --
// confirmed live: the rendered table order didn't match either the saved
// preference or the Manage-columns popover's own list until this was added.
const columnDefs = {
  symbol: columnHelper.accessor('symbol', {
    id: 'symbol',
    header: 'Symbol',
    cell: (info) => <span className="font-semibold text-slate-100">{info.getValue()}</span>,
  }),
  source: columnHelper.accessor('strategySource', {
    id: 'source',
    header: 'Source',
    cell: (info) => <span className="text-muted">{info.getValue()}</span>,
  }),
  direction: columnHelper.accessor('direction', {
    id: 'direction',
    header: 'Direction',
    cell: (info) => <DirectionBadge direction={info.getValue()} />,
  }),
  qty: columnHelper.accessor('size_units', {
    id: 'qty',
    header: 'Qty',
    cell: (info) => <span className="tabular-nums">{fmtQty(info.getValue())}</span>,
  }),
  entry: columnHelper.accessor('entry_price', {
    id: 'entry',
    header: 'Entry',
    cell: (info) => <span className="tabular-nums">{fmt(info.getValue())}</span>,
  }),
  current: columnHelper.accessor('current_price', {
    id: 'current',
    header: 'Current',
    cell: (info) => <span className="tabular-nums font-medium text-slate-200">{fmt(info.getValue())}</span>,
  }),
  value: columnHelper.accessor('currentVal', {
    id: 'value',
    header: 'Value',
    cell: (info) => <span className="tabular-nums">{fmt(info.getValue())}</span>,
  }),
  pnl: columnHelper.accessor('unrealised_pnl', {
    id: 'pnl',
    header: 'P&L',
    cell: (info) => {
      const v = info.getValue() ?? 0;
      return (
        <span className={`tabular-nums font-semibold ${v >= 0 ? 'text-profit' : 'text-loss'}`}>
          {v >= 0 ? '+' : ''}{fmt(v)}
        </span>
      );
    },
  }),
  pnlPct: columnHelper.accessor('unrealised_pct', {
    id: 'pnlPct',
    header: 'P&L %',
    cell: (info) => <PnLPct value={info.getValue()} />,
  }),
  slTpDistance: columnHelper.display({
    id: 'slTpDistance',
    header: 'SL/TP Distance',
    cell: ({ row }) => {
      const { slDist, tpDist } = row.original;
      return (
        <span className="text-[11px] tabular-nums">
          <span className="text-loss">SL {slDist != null ? `${slDist.toFixed(1)}%` : '—'}</span>
          <span className="text-muted mx-1">/</span>
          <span className="text-profit">TP {tpDist != null ? `${tpDist.toFixed(1)}%` : '—'}</span>
        </span>
      );
    },
  }),
  holdingTime: columnHelper.display({
    id: 'holdingTime',
    header: 'Holding',
    cell: ({ row }) => <span className="text-muted tabular-nums">{elapsed(row.original.opened_at)}</span>,
  }),
};

const ROW_HEIGHT = 44;
const VIRTUALIZE_THRESHOLD = 30;

export default function PositionsTable({ positions, onSelectPosition }) {
  const { prefs, setPrefs } = useTradesPreferences();
  const columnsPref = prefs.positionsColumns;
  const scrollRef = useRef(null);

  const data = useMemo(() => positions.map(deriveRow), [positions]);

  const columnOrder = columnsPref.order;
  const columnVisibility = useMemo(() => {
    const v = {};
    for (const c of POSITION_COLUMNS) v[c.id] = !columnsPref.hidden.includes(c.id);
    return v;
  }, [columnsPref.hidden]);

  const columns = useMemo(
    () => columnsPref.order.map((id) => columnDefs[id]).filter(Boolean),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const table = useReactTable({
    data,
    columns,
    state: { columnOrder, columnVisibility },
    onColumnOrderChange: (updater) => {
      const next = typeof updater === 'function' ? updater(columnOrder) : updater;
      setPrefs((p) => ({ ...p, positionsColumns: { ...p.positionsColumns, order: next } }));
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const visibleRows = table.getRowModel().rows;
  const shouldVirtualize = visibleRows.length > VIRTUALIZE_THRESHOLD;

  const virtualizer = useVirtualizer({
    count: visibleRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    enabled: shouldVirtualize,
  });

  const visibleLeafColumns = table.getVisibleLeafColumns();
  const gridTemplate = visibleLeafColumns
    .map((c) => `${COLUMN_WIDTH[c.id] ?? 100}px`)
    .join(' ');

  const renderRow = (row, style) => (
    <div
      key={row.id}
      role="row"
      tabIndex={0}
      data-position-nav="true"
      aria-label={`${row.original.symbol} position, press Enter for details`}
      style={{ ...style, display: 'grid', gridTemplateColumns: gridTemplate }}
      className="items-center border-b border-border/50 px-3 text-xs hover:bg-white/[0.02] cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent transition-colors"
      onClick={() => onSelectPosition?.(row.original)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelectPosition?.(row.original);
        }
      }}
    >
      {row.getVisibleCells().map((cell) => (
        <div key={cell.id} role="cell" className="py-2 pr-3 truncate">
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </div>
      ))}
    </div>
  );

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      <div className="flex items-center justify-end px-3 py-2 border-b border-border">
        <ColumnManagerPopover
          columns={columnsPref}
          onChange={(next) => setPrefs((p) => ({ ...p, positionsColumns: next }))}
        />
      </div>

      {/* Header */}
      <div
        role="row"
        style={{ display: 'grid', gridTemplateColumns: gridTemplate }}
        className="items-center px-3 py-2 border-b border-border bg-white/[0.02] text-[10px] uppercase tracking-wide text-muted font-medium"
      >
        {table.getFlatHeaders().map((header) => {
          const sorted = header.column.getIsSorted();
          return (
            <button
              key={header.id}
              role="columnheader"
              type="button"
              onClick={header.column.getToggleSortingHandler()}
              className="flex items-center gap-1 pr-3 text-left hover:text-slate-200 transition-colors"
              aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none'}
            >
              {flexRender(header.column.columnDef.header, header.getContext())}
              {sorted === 'asc' ? <ArrowUp size={10} aria-hidden="true" />
                : sorted === 'desc' ? <ArrowDown size={10} aria-hidden="true" />
                : <ArrowUpDown size={10} className="opacity-30" aria-hidden="true" />}
            </button>
          );
        })}
      </div>

      {/* Body */}
      {!shouldVirtualize ? (
        <div role="rowgroup">
          {visibleRows.map((row) => renderRow(row, { height: ROW_HEIGHT }))}
        </div>
      ) : (
        <div
          ref={scrollRef}
          role="rowgroup"
          className="overflow-y-auto"
          style={{ maxHeight: '70vh' }}
        >
          <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
            {virtualizer.getVirtualItems().map((virtualRow) =>
              renderRow(visibleRows[virtualRow.index], {
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }),
            )}
          </div>
        </div>
      )}
    </div>
  );
}
