import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import { TrendingUp, RefreshCw, AlertCircle, ArrowUpRight, Clock, IndianRupee } from 'lucide-react';

function fmt(n) {
  if (n == null) return '—';
  return '₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
}

function fmtCr(n) {
  if (n == null) return '—';
  return '₹' + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(n) + ' Cr';
}

function StatusBadge({ status }) {
  const cfg = {
    OPEN:     { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', label: 'Open' },
    UPCOMING: { cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30',         label: 'Upcoming' },
    CLOSED:   { cls: 'bg-slate-500/15 text-slate-400 border-slate-500/30',       label: 'Closed' },
  }[status] ?? { cls: 'bg-slate-700/30 text-slate-400 border-slate-600', label: status };
  return (
    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}

function TypeBadge({ type }) {
  const isOpen = type === 'OPEN_MARKET';
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
      isOpen ? 'bg-purple-500/15 text-purple-400 border-purple-500/30'
             : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    }`}>
      {isOpen ? 'Open Market' : 'Tender Offer'}
    </span>
  );
}

function SpreadBar({ pct }) {
  if (pct == null) return <span className="text-slate-500 text-sm">—</span>;
  const positive = pct >= 0;
  const width = Math.min(Math.abs(pct), 100);
  return (
    <div className="flex items-center gap-2">
      <span className={`font-bold text-sm tabular-nums ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
        {positive ? '+' : ''}{pct.toFixed(2)}%
      </span>
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden max-w-24">
        <div
          className={`h-full rounded-full transition-all ${positive ? 'bg-emerald-500' : 'bg-red-500'}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

function BuybackCard({ b }) {
  const isOpportunity = b.opportunity && b.market_price;
  return (
    <div className={`relative bg-[#0F1629] border rounded-xl p-5 transition-all hover:border-slate-600 ${
      isOpportunity
        ? 'border-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.06)]'
        : 'border-slate-700/50'
    }`}>
      {/* Opportunity badge */}
      {isOpportunity && (
        <div className="absolute -top-2.5 left-4">
          <span className="bg-emerald-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-1">
            <ArrowUpRight size={10} /> ARBITRAGE OPPORTUNITY
          </span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-slate-100 font-bold text-base">
              {b.symbol.replace('.NS', '').replace('.BO', '')}
            </span>
            <StatusBadge status={b.status} />
            <TypeBadge type={b.buyback_type} />
          </div>
          <p className="text-slate-400 text-xs">{b.company_name}</p>
        </div>
        {b.total_size_cr && (
          <div className="text-right shrink-0">
            <p className="text-slate-300 font-semibold text-sm">{fmtCr(b.total_size_cr)}</p>
            <p className="text-slate-500 text-[10px]">total size</p>
          </div>
        )}
      </div>

      {/* Price comparison */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="bg-[#080D1A] rounded-lg p-3 text-center">
          <p className="text-slate-400 text-[10px] uppercase tracking-wide mb-1">Buyback Price</p>
          <p className="text-white font-bold text-lg">{fmt(b.buyback_price)}</p>
        </div>
        <div className="bg-[#080D1A] rounded-lg p-3 text-center">
          <p className="text-slate-400 text-[10px] uppercase tracking-wide mb-1">Market Price</p>
          <p className={`font-bold text-lg ${b.market_price ? 'text-white' : 'text-slate-500'}`}>
            {b.market_price ? fmt(b.market_price) : 'Loading…'}
          </p>
        </div>
        <div className={`rounded-lg p-3 text-center ${isOpportunity ? 'bg-emerald-500/10' : 'bg-[#080D1A]'}`}>
          <p className="text-slate-400 text-[10px] uppercase tracking-wide mb-1">Spread</p>
          <div className="flex justify-center">
            <SpreadBar pct={b.spread_pct} />
          </div>
        </div>
      </div>

      {/* Dates */}
      <div className="flex flex-wrap gap-4 text-xs text-slate-500">
        {b.record_date && (
          <span className="flex items-center gap-1">
            <Clock size={11} /> Record: <span className="text-slate-300">{b.record_date}</span>
          </span>
        )}
        {b.open_date && (
          <span className="flex items-center gap-1">
            Open: <span className="text-slate-300">{b.open_date}</span>
          </span>
        )}
        {b.close_date && (
          <span className="flex items-center gap-1">
            Close: <span className="text-slate-300">{b.close_date}</span>
          </span>
        )}
        {b.last_refreshed && (
          <span className="flex items-center gap-1 ml-auto">
            <Clock size={10} /> Updated {new Date(b.last_refreshed).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {/* Opportunity explanation */}
      {isOpportunity && (
        <div className="mt-3 p-3 bg-emerald-500/5 border border-emerald-500/20 rounded-lg text-xs text-emerald-300">
          <strong>Buy opportunity:</strong> Market price ({fmt(b.market_price)}) is below the buyback
          offer price ({fmt(b.buyback_price)}). Tendering in the buyback gives a locked-in
          profit of {fmt(b.buyback_price - b.market_price)} per share (+{b.spread_pct?.toFixed(2)}%).
        </div>
      )}
    </div>
  );
}

export default function BuybackTracker() {
  const [data,        setData]        = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [refreshing,  setRefreshing]  = useState(false);
  const [error,       setError]       = useState(null);
  const [filter,      setFilter]      = useState('ALL'); // ALL | OPPORTUNITY | TENDER | OPEN_MARKET

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/buyback/');
      setData(Array.isArray(res) ? res : []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000); // refresh prices every 30s
    return () => clearInterval(id);
  }, [load]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await apiFetch('/api/v1/buyback/refresh', { method: 'POST' });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  const filtered = data.filter(b => {
    if (filter === 'OPPORTUNITY')  return b.opportunity;
    if (filter === 'TENDER')       return b.buyback_type === 'TENDER';
    if (filter === 'OPEN_MARKET')  return b.buyback_type === 'OPEN_MARKET';
    return true;
  });

  const opportunities = data.filter(b => b.opportunity).length;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <TrendingUp className="text-emerald-400" size={24} />
            Buyback Tracker
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            NSE/BSE corporate buyback offers — live spread vs market price
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Refreshing…' : 'Refresh Data'}
        </button>
      </div>

      {/* Stats row */}
      {!loading && data.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Active Offers',       value: data.length,     color: 'text-white' },
            { label: 'Opportunities',        value: opportunities,   color: 'text-emerald-400' },
            { label: 'Tender Offers',        value: data.filter(b => b.buyback_type === 'TENDER').length, color: 'text-amber-400' },
            { label: 'Open Market',          value: data.filter(b => b.buyback_type === 'OPEN_MARKET').length, color: 'text-purple-400' },
          ].map(s => (
            <div key={s.label} className="bg-[#0F1629] border border-slate-700/50 rounded-xl p-4 text-center">
              <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
              <p className="text-slate-400 text-xs mt-1">{s.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-2 flex-wrap">
        {[
          { key: 'ALL',          label: 'All Offers' },
          { key: 'OPPORTUNITY',  label: `Arbitrage (${opportunities})` },
          { key: 'TENDER',       label: 'Tender Offer' },
          { key: 'OPEN_MARKET',  label: 'Open Market' },
        ].map(f => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
              filter === f.key
                ? 'bg-indigo-600 text-white'
                : 'bg-[#0F1629] border border-slate-700/50 text-slate-400 hover:text-slate-200'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl p-4">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="bg-[#0F1629] border border-slate-700/50 rounded-xl p-5 animate-pulse">
              <div className="h-4 bg-slate-700 rounded w-1/3 mb-3" />
              <div className="h-3 bg-slate-800 rounded w-2/3 mb-4" />
              <div className="grid grid-cols-3 gap-3">
                {[1,2,3].map(j => <div key={j} className="h-16 bg-slate-800 rounded-lg" />)}
              </div>
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-slate-500">
          <TrendingUp size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-lg font-medium">No buyback offers found</p>
          <p className="text-sm mt-1">Click "Refresh Data" to fetch latest NSE announcements</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map(b => <BuybackCard key={b.id} b={b} />)}
        </div>
      )}

      {/* Explanation */}
      <div className="bg-[#0F1629] border border-slate-700/30 rounded-xl p-5 text-sm text-slate-400 space-y-2">
        <p className="font-semibold text-slate-200">How buyback arbitrage works (NSE)</p>
        <p>When a company announces a buyback at ₹X, if the market price is currently below ₹X, you can buy
           shares in the open market and tender them in the buyback for a risk-free profit.</p>
        <p><span className="text-emerald-400 font-medium">Tender Offer:</span> Company buys shares at a fixed price during a short window (usually 2 weeks).
           You submit your shares via your broker before the close date.</p>
        <p><span className="text-purple-400 font-medium">Open Market:</span> Company gradually buys from the exchange over months at prevailing market prices.</p>
        <p className="text-xs text-slate-500">Note: Acceptance ratio in oversubscribed buybacks may reduce actual shares tendered. Always check the offer document.</p>
      </div>
    </div>
  );
}
