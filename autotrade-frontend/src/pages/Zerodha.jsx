import { useState, useEffect, useCallback, useRef } from 'react';
import toast from 'react-hot-toast';
import {
  Wifi, WifiOff, RefreshCw, ExternalLink, LogOut,
  TrendingUp, TrendingDown, IndianRupee, Zap,
  CheckCircle, Clock, XCircle, MinusCircle, AlertTriangle,
} from 'lucide-react';
import {
  getZerodhaStatus, getZerodhaLoginUrl, logoutZerodha,
  getZerodhaHoldings, getZerodhaOrders, getZerodhaTrades,
  getZerodhaPnl, getZerodhaLivePrices,
} from '../api/client';

// ── Formatters ────────────────────────────────────────────────────────────────

const inr = (n, dec = 2) => {
  if (n == null) return '—';
  return '₹' + Number(n).toLocaleString('en-IN', {
    minimumFractionDigits: dec, maximumFractionDigits: dec,
  });
};

const pct = (n) => {
  if (n == null) return '—';
  const v = Number(n);
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
};

const pos = (n) => Number(n) >= 0;

function PnlCell({ value, showPct, pctValue }) {
  if (value == null) return <span className="text-muted">—</span>;
  return (
    <div>
      <span className={pos(value) ? 'text-emerald-400' : 'text-rose-400'}>
        {pos(value) ? '+' : ''}{inr(value)}
      </span>
      {showPct && pctValue != null && (
        <div className={`text-[11px] ${pos(pctValue) ? 'text-emerald-400/70' : 'text-rose-400/70'}`}>
          {pct(pctValue)}
        </div>
      )}
    </div>
  );
}

function Badge({ status }) {
  const map = {
    COMPLETE:  { color: 'bg-emerald-500/15 text-emerald-400', icon: CheckCircle },
    OPEN:      { color: 'bg-blue-500/15 text-blue-400',       icon: Clock },
    PENDING:   { color: 'bg-blue-500/15 text-blue-400',       icon: Clock },
    CANCELLED: { color: 'bg-slate-500/15 text-slate-400',     icon: MinusCircle },
    REJECTED:  { color: 'bg-rose-500/15 text-rose-400',       icon: XCircle },
  };
  const cfg = map[status] || { color: 'bg-slate-500/15 text-slate-400', icon: MinusCircle };
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${cfg.color}`}>
      <Icon size={10} />
      {status}
    </span>
  );
}

// ── Section 1 — Connection Status ─────────────────────────────────────────────

function ConnectionCard({ status, onConnect, onDisconnect }) {
  if (!status) return null;

  if (status.connected) {
    return (
      <div className="rounded-xl border border-emerald-500/20 p-5"
        style={{ background: 'rgba(16,185,129,0.06)' }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-500/15">
              <Wifi size={18} className="text-emerald-400" />
            </div>
            <div>
              <p className="text-emerald-400 font-bold text-base">Zerodha Connected</p>
              <p className="text-slate-400 text-sm mt-0.5">
                {status.user_name} · {status.user_id} · {status.email}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-muted text-xs">Available Cash</p>
              <p className="text-slate-100 font-bold text-lg">{inr(status.available_margins_inr)}</p>
              <p className="text-muted text-xs mt-0.5">Token expires: {status.token_expires_at}</p>
            </div>
            <button onClick={onDisconnect}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-rose-500/30 text-rose-400 text-sm hover:bg-rose-500/10 transition-all">
              <LogOut size={13} />
              Disconnect
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-amber-500/20 p-5"
      style={{ background: 'rgba(245,158,11,0.06)' }}>
      <div className="flex items-start gap-4">
        <div className="p-2 rounded-lg bg-amber-500/15 shrink-0">
          <WifiOff size={18} className="text-amber-400" />
        </div>
        <div className="flex-1">
          <p className="text-amber-400 font-bold text-base mb-1">Zerodha Not Connected</p>
          <p className="text-slate-400 text-sm mb-4">
            Connect your Zerodha account to see your real portfolio, live prices, and enable real trading.
          </p>
          {status.api_key_configured === false ? (
            <p className="text-muted text-sm">
              Set <code className="text-cyan bg-white/5 px-1 rounded">ZERODHA_API_KEY</code> and{' '}
              <code className="text-cyan bg-white/5 px-1 rounded">ZERODHA_API_SECRET</code> in{' '}
              <code className="text-cyan bg-white/5 px-1 rounded">.env</code> first.
            </p>
          ) : (
            <>
              <button onClick={onConnect}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-blue-600 to-cyan-600 text-white font-semibold text-sm hover:opacity-90 transition-all mb-4">
                <ExternalLink size={14} />
                Connect Zerodha Account
              </button>
              <ol className="text-slate-500 text-xs space-y-1">
                <li>1. Click the button above to open Zerodha login</li>
                <li>2. Log in with your Zerodha ID and TOTP</li>
                <li>3. You will be redirected back automatically</li>
                <li>4. This page will refresh when connected</li>
              </ol>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Section 2 — Portfolio Summary ─────────────────────────────────────────────

function PortfolioSummary({ pnl }) {
  if (!pnl) return null;
  const cards = [
    { label: 'Real Portfolio Value', value: inr(pnl.total_equity), sub: null },
    { label: 'Invested (Demat)',     value: inr(pnl.demat_invested), sub: null },
    { label: 'Unrealised P&L',       value: inr(pnl.demat_pnl),    sub: pct(pnl.demat_pnl_pct),   gain: pos(pnl.demat_pnl) },
    { label: "Today's P&L",          value: inr(pnl.today_pnl),     sub: null,                     gain: pos(pnl.today_pnl) },
  ];
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map(({ label, value, sub, gain }) => (
        <div key={label} className="card p-4">
          <p className="text-muted text-xs mb-2">{label}</p>
          <p className={`text-xl font-bold ${gain != null ? (gain ? 'text-emerald-400' : 'text-rose-400') : 'text-slate-100'}`}>
            {value}
          </p>
          {sub && <p className={`text-xs mt-0.5 ${gain ? 'text-emerald-400/70' : 'text-rose-400/70'}`}>{sub}</p>}
        </div>
      ))}
    </div>
  );
}

// ── Section 2 — Holdings table ─────────────────────────────────────────────

function HoldingsTable({ holdings, loading }) {
  if (loading) return <div className="card p-8 text-center text-muted text-sm">Loading holdings…</div>;
  if (!holdings?.length) {
    return (
      <div className="card p-8 text-center">
        <p className="text-muted text-sm">No Demat holdings found.</p>
        <p className="text-muted/60 text-xs mt-1">Holdings sync from Zerodha once connected.</p>
      </div>
    );
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            {['Stock','Qty','Avg Price','LTP','Day Change','P&L','P&L %'].map(h => (
              <th key={h} className={`px-4 py-3 ${h === 'Stock' ? 'text-left' : 'text-right'}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {holdings.map((h, i) => (
            <tr key={i} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3">
                <div className="font-semibold text-slate-200">{h.tradingsymbol}</div>
                <div className="text-xs text-muted">{h.exchange}</div>
              </td>
              <td className="px-4 py-3 text-right text-slate-300">{h.quantity}</td>
              <td className="px-4 py-3 text-right text-slate-300">{inr(h.average_price)}</td>
              <td className="px-4 py-3 text-right font-medium text-slate-100">{inr(h.last_price)}</td>
              <td className="px-4 py-3 text-right">
                <span className={pos(h.day_change) ? 'text-emerald-400' : 'text-rose-400'}>
                  {pct(h.day_change_percentage)}
                </span>
              </td>
              <td className="px-4 py-3 text-right">
                <PnlCell value={h.pnl} />
              </td>
              <td className="px-4 py-3 text-right">
                <PnlCell value={h.pnl_percentage} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Section 3 — Orders ────────────────────────────────────────────────────────

function OrdersTable({ orders }) {
  if (!orders?.length) {
    return <div className="card p-6 text-center text-muted text-sm">No orders today.</div>;
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            {['Symbol','Type','Qty','Price','Status','Time'].map(h => (
              <th key={h} className={`px-4 py-3 ${h === 'Symbol' ? 'text-left' : 'text-right'}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {orders.map((o, i) => (
            <tr key={i} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3 font-semibold text-slate-200">{o.tradingsymbol}</td>
              <td className="px-4 py-3 text-right">
                <span className={o.transaction_type === 'BUY' ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
                  {o.transaction_type}
                </span>
              </td>
              <td className="px-4 py-3 text-right text-slate-300">{o.quantity}</td>
              <td className="px-4 py-3 text-right text-slate-300">{inr(o.price || o.average_price)}</td>
              <td className="px-4 py-3 text-right"><Badge status={o.status} /></td>
              <td className="px-4 py-3 text-right text-muted text-xs">
                {o.order_timestamp ? new Date(o.order_timestamp).toLocaleTimeString('en-IN') : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Section 4 — Paper mode warning ───────────────────────────────────────────

function PaperModeBanner() {
  return (
    <div className="flex items-start gap-3 p-4 rounded-xl border border-amber-500/25"
      style={{ background: 'rgba(245,158,11,0.06)' }}>
      <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
      <div>
        <p className="text-amber-400 font-semibold text-sm">AutoTrade Pro is in PAPER TRADING mode</p>
        <p className="text-slate-400 text-xs mt-1">
          Your Zerodha account is connected for <strong>viewing only</strong>.
          No real orders will be placed automatically.
          To enable real trading, set <code className="bg-white/5 px-1 rounded">PAPER_MODE=false</code> in Settings.
        </p>
      </div>
    </div>
  );
}

// ── Section 5 — Live Prices ───────────────────────────────────────────────────

function LivePricesPanel({ prices }) {
  const entries = Object.entries(prices || {});
  if (!entries.length) {
    return (
      <div className="card p-6 text-center text-muted text-sm">
        Live prices stream once WebSocket connects or during NSE market hours.
      </div>
    );
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            <th className="px-4 py-3 text-left">Symbol</th>
            <th className="px-4 py-3 text-right">LTP</th>
            <th className="px-4 py-3 text-right">Open</th>
            <th className="px-4 py-3 text-right">High</th>
            <th className="px-4 py-3 text-right">Low</th>
            <th className="px-4 py-3 text-right">Volume</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {entries.map(([sym, d]) => (
            <tr key={sym} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3 font-semibold text-slate-200">
                {sym.replace('.NS', '')}
              </td>
              <td className="px-4 py-3 text-right text-slate-100 font-medium">{inr(d.price)}</td>
              <td className="px-4 py-3 text-right text-muted">{inr(d.open)}</td>
              <td className="px-4 py-3 text-right text-emerald-400">{inr(d.high)}</td>
              <td className="px-4 py-3 text-right text-rose-400">{inr(d.low)}</td>
              <td className="px-4 py-3 text-right text-muted">
                {d.volume != null ? Number(d.volume).toLocaleString('en-IN') : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Zerodha() {
  const [status,   setStatus]   = useState(null);
  const [holdings, setHoldings] = useState([]);
  const [orders,   setOrders]   = useState([]);
  const [pnl,      setPnl]      = useState(null);
  const [prices,   setPrices]   = useState({});
  const [loading,  setLoading]  = useState(true);
  const [hlLoading, setHlLoading] = useState(false);

  const wsRef = useRef(null);

  const fetchStatus = useCallback(async () => {
    try { setStatus(await getZerodhaStatus()); } catch { /* not configured */ }
  }, []);

  const fetchAll = useCallback(async (connected) => {
    if (!connected) return;
    setHlLoading(true);
    try {
      const [h, o, p, lp] = await Promise.allSettled([
        getZerodhaHoldings(),
        getZerodhaOrders(),
        getZerodhaPnl(),
        getZerodhaLivePrices(),
      ]);
      if (h.status  === 'fulfilled') setHoldings(h.value?.holdings ?? []);
      if (o.status  === 'fulfilled') setOrders(o.value?.orders ?? []);
      if (p.status  === 'fulfilled') setPnl(p.value);
      if (lp.status === 'fulfilled') setPrices(lp.value?.prices ?? {});
    } finally {
      setHlLoading(false);
    }
  }, []);

  useEffect(() => {
    const init = async () => {
      await fetchStatus();
      setLoading(false);
    };
    init();
    const id = setInterval(fetchStatus, 20_000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // When connected, load portfolio data + start price polling
  useEffect(() => {
    if (!status) return;
    if (status.connected) {
      fetchAll(true);
      const id = setInterval(() => fetchAll(true), 15_000);
      return () => clearInterval(id);
    }
  }, [status?.connected, fetchAll]);

  async function handleConnect() {
    try {
      const { url } = await getZerodhaLoginUrl();
      window.open(url, '_blank', 'noopener');
      toast('Login in the popup window — this page will update automatically.');
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not fetch login URL');
    }
  }

  async function handleDisconnect() {
    try {
      await logoutZerodha();
      toast.success('Disconnected from Zerodha');
      setStatus(null);
      setHoldings([]);
      setOrders([]);
      setPnl(null);
      setPrices({});
      await fetchStatus();
    } catch {
      toast.error('Disconnect failed');
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw size={22} className="animate-spin text-cyan" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-2">
            <Zap size={22} className="text-cyan" />
            Zerodha KiteConnect
          </h1>
          <p className="text-muted text-sm mt-0.5">Real portfolio, live prices, and order management</p>
        </div>
        {status?.connected && (
          <button onClick={() => fetchAll(true)} disabled={hlLoading}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/10 text-sm text-slate-300 hover:text-white hover:bg-white/5 disabled:opacity-50 transition-all">
            <RefreshCw size={13} className={hlLoading ? 'animate-spin' : ''} />
            Refresh
          </button>
        )}
      </div>

      {/* Section 4 — always visible paper mode warning */}
      <PaperModeBanner />

      {/* Section 1 — Connection card */}
      <ConnectionCard
        status={status}
        onConnect={handleConnect}
        onDisconnect={handleDisconnect}
      />

      {status?.connected && (
        <>
          {/* Section 2 — Portfolio */}
          <PortfolioSummary pnl={pnl} />

          <div>
            <h2 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
              <IndianRupee size={14} className="text-cyan" />
              Demat Holdings
            </h2>
            <HoldingsTable holdings={holdings} loading={hlLoading && !holdings.length} />
          </div>

          {/* Section 3 — Orders */}
          <div>
            <h2 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
              {orders.some(o => o.transaction_type === 'BUY')
                ? <TrendingUp size={14} className="text-emerald-400" />
                : <TrendingDown size={14} className="text-rose-400" />}
              Today's Orders
              <span className="ml-1 text-xs text-muted">({orders.length})</span>
            </h2>
            <OrdersTable orders={orders} />
          </div>

          {/* Section 5 — Live prices */}
          <div>
            <h2 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
              <Zap size={14} className="text-cyan" />
              Live Prices
              <span className="ml-1 text-xs text-muted/60">(WebSocket / REST)</span>
            </h2>
            <LivePricesPanel prices={prices} />
          </div>
        </>
      )}

    </div>
  );
}
