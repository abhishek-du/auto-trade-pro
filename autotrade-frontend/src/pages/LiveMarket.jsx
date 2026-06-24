import { useState, useMemo } from 'react';
import { RefreshCw, ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';
import { useLiveMarket } from '../hooks/useLiveMarket';
import { refreshLivePrices } from '../api/client';
import MarketStatusBar  from '../components/market/MarketStatusBar';
import IndexCard        from '../components/market/IndexCard';
import StockTickerRow   from '../components/market/StockTickerRow';
import TopMoversPanel   from '../components/market/TopMoversPanel';
import { BreadthWidget } from '../components/breadth/BreadthWidget';
import SectorHeatmapWidget from '../components/heatmap/SectorHeatmapWidget';
import toast            from 'react-hot-toast';

const FILTERS = ['All', 'Stocks', 'Commodities', 'Forex'];

const SORT_COLS = ['name', 'price', 'change_pct', 'volume'];

function SortIcon({ col, sortCol, sortDir }) {
  if (col !== sortCol) return <ChevronsUpDown size={12} className="text-muted/50" />;
  return sortDir === 'asc'
    ? <ChevronUp   size={12} className="text-cyan" />
    : <ChevronDown size={12} className="text-cyan" />;
}

export default function LiveMarket() {
  const { prices, summary, topMovers, connected, lastUpdated } = useLiveMarket();

  const [filter,    setFilter]    = useState('All');
  const [sortCol,   setSortCol]   = useState('change_pct');
  const [sortDir,   setSortDir]   = useState('desc');
  const [refreshing, setRefreshing] = useState(false);

  // Build flat array of all price entries
  const allItems = useMemo(() => Object.values(prices), [prices]);

  // Filtered items for the table (stocks, commodities, forex — not indices)
  const tableItems = useMemo(() => {
    let items = allItems.filter(item => {
      const t = item.type ?? 'stock';
      if (filter === 'Stocks')      return t === 'stock';
      if (filter === 'Commodities') return t === 'commodity';
      if (filter === 'Forex')       return t === 'forex';
      return t !== 'index'; // All = non-index
    });

    items = [...items].sort((a, b) => {
      let av = a[sortCol] ?? 0;
      let bv = b[sortCol] ?? 0;
      if (sortCol === 'name') { av = a.name ?? ''; bv = b.name ?? ''; }
      if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === 'asc' ? av - bv : bv - av;
    });

    return items;
  }, [allItems, filter, sortCol, sortDir]);

  function toggleSort(col) {
    if (col === sortCol) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await refreshLivePrices();
      toast.success('Prices refreshed');
    } catch {
      toast.error('Refresh failed');
    } finally {
      setRefreshing(false);
    }
  }

  const vix = prices['^INDIAVIX'];

  return (
    <div className="space-y-5">

      {/* 1 — Status bar */}
      <MarketStatusBar
        summary={summary}
        connected={connected}
        lastUpdated={lastUpdated}
      />

      {/* 2 — Three index cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <IndexCard data={prices['^NSEI']}    />
        <IndexCard data={prices['^NSEBANK']} />
        <IndexCard data={prices['^BSESN']}   />
      </div>

      {/* 3 — VIX + breadth (left) | sector heatmap (right) */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 space-y-4">
          {/* VIX card */}
          {vix && (
            <div className="glass-panel border border-border rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <p className="text-slate-200 text-sm font-semibold">India VIX</p>
                <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                  (vix.price ?? 0) > 20 ? 'bg-loss/15 text-loss' :
                  (vix.price ?? 0) > 15 ? 'bg-warn/15 text-warn' :
                  'bg-profit/15 text-profit'
                }`}>
                  {(vix.price ?? 0) > 20 ? 'High Volatility' :
                   (vix.price ?? 0) > 15 ? 'Moderate' : 'Low Volatility'}
                </span>
              </div>
              <div className="flex items-end gap-3">
                <p className="text-3xl font-extrabold text-slate-100 tabular-nums">
                  {Number(vix.price ?? 0).toFixed(2)}
                </p>
                <p className={`text-sm tabular-nums mb-0.5 font-semibold ${(vix.change_pct ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {(vix.change_pct ?? 0) >= 0 ? '▲' : '▼'} {Math.abs(vix.change_pct ?? 0).toFixed(2)}%
                </p>
              </div>
              <p className="text-muted text-xs mt-1">Fear gauge — lower is calmer</p>
            </div>
          )}
          <BreadthWidget compact={false} />
        </div>
        <div className="lg:col-span-3">
          <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
            <p className="text-slate-200 text-sm font-semibold">Sector Heatmap</p>
            <SectorHeatmapWidget compact={false} maxSectors={10} />
          </div>
        </div>
      </div>

      {/* 4 — Top movers */}
      <TopMoversPanel topMovers={topMovers} />

      {/* 5 — Full stocks table */}
      <div className="glass-panel border border-border rounded-xl overflow-hidden">

        {/* Table header */}
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-border">
          <h2 className="text-slate-200 font-semibold text-sm">Live Prices</h2>

          <div className="flex items-center gap-2 flex-wrap">
            {/* Filter tabs */}
            <div className="flex rounded-lg overflow-hidden border border-border">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={[
                    'px-3 py-1.5 text-xs font-medium transition-colors',
                    filter === f ? 'bg-accent text-white' : 'text-muted hover:text-slate-300 hover:bg-surface',
                  ].join(' ')}
                >
                  {f}
                </button>
              ))}
            </div>

            {/* Refresh button */}
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-slate-300 hover:text-white hover:bg-white/5 disabled:opacity-50 transition-all"
            >
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
              Refresh
            </button>

            <span className="text-muted text-xs">{tableItems.length} symbols</span>
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {[
                  { col: 'name',       label: 'Stock'      },
                  { col: 'price',      label: 'LTP'        },
                  { col: 'change',     label: 'Change'     },
                  { col: 'change_pct', label: 'Change %'   },
                  { col: 'volume',     label: 'Volume'     },
                  { col: null,         label: '52W Position' },
                ].map(({ col, label }) => (
                  <th
                    key={label}
                    onClick={col ? () => toggleSort(col) : undefined}
                    className={[
                      'px-4 py-3 text-left text-muted text-xs font-semibold uppercase tracking-wider whitespace-nowrap',
                      col ? 'cursor-pointer hover:text-slate-300 select-none' : '',
                    ].join(' ')}
                  >
                    <span className="flex items-center gap-1">
                      {label}
                      {col && <SortIcon col={col} sortCol={sortCol} sortDir={sortDir} />}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableItems.length === 0 ? (
                <tr>
                  <td colSpan={6} className="text-center py-12 text-muted text-sm">
                    Waiting for price data…
                  </td>
                </tr>
              ) : (
                tableItems.map((item) => (
                  <StockTickerRow
                    key={item.symbol}
                    symbol={item.symbol}
                    name={item.name}
                    price={item.price}
                    change={item.change}
                    change_pct={item.change_pct}
                    volume={item.volume}
                    type={item.type}
                    w52_low={item['52w_low']}
                    w52_high={item['52w_high']}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-border flex items-center justify-between flex-wrap gap-2">
          <span className="text-muted text-xs">
            Data from yfinance — refreshes every 15 s during market hours, 60 s otherwise
          </span>
          <span className="text-cyan/70 text-xs">
            Upgrade to Zerodha KiteConnect (paid plan) for real-time tick data
          </span>
        </div>
      </div>
    </div>
  );
}
