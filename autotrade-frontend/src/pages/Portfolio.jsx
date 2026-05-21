import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import {
  Briefcase, RefreshCw, ExternalLink, Unlink, Plus,
  TrendingUp, TrendingDown, IndianRupee, Percent,
} from 'lucide-react';
import {
  getKiteStatus, getKiteLoginUrl, getKiteHoldings,
  syncKiteHoldings, disconnectKite, addManualHolding,
} from '../api/client';

// ── Helper formatters ─────────────────────────────────────────────────────────

function fmt(n, dec = 2) {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-IN', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function pct(n) {
  if (n == null) return '—';
  const v = Number(n);
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function PnlBadge({ value }) {
  if (value == null) return <span className="text-muted">—</span>;
  const pos = Number(value) >= 0;
  return (
    <span className={pos ? 'text-emerald-400' : 'text-rose-400'}>
      {pos ? '+' : ''}₹{fmt(Math.abs(value))}
    </span>
  );
}

function PctBadge({ value }) {
  if (value == null) return <span className="text-muted">—</span>;
  const pos = Number(value) >= 0;
  return (
    <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${pos ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'}`}>
      {pct(value)}
    </span>
  );
}

// ── Connection Banner ─────────────────────────────────────────────────────────

function ConnectionBanner({ status, onConnect, onSync, onDisconnect, syncing }) {
  if (!status) return null;

  if (status.connected) {
    return (
      <div className="flex items-center justify-between p-4 rounded-xl border border-emerald-500/20"
        style={{ background: 'rgba(16,185,129,0.06)' }}>
        <div className="flex items-center gap-3">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse shrink-0" />
          <div>
            <p className="text-emerald-400 font-semibold text-sm">Zerodha Kite Connected</p>
            <p className="text-muted text-xs mt-0.5">
              {status.holdings_count} holdings synced · expires {status.expires_at ? new Date(status.expires_at).toLocaleTimeString('en-IN') : '—'}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onSync}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-white/10 text-slate-300 hover:text-white hover:bg-white/5 disabled:opacity-50 transition-all"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            Sync Now
          </button>
          <button
            onClick={onDisconnect}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-rose-500/30 text-rose-400 hover:bg-rose-500/10 transition-all"
          >
            <Unlink size={12} />
            Disconnect
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between p-4 rounded-xl border border-amber-500/20"
      style={{ background: 'rgba(245,158,11,0.06)' }}>
      <div className="flex items-center gap-3">
        <span className="w-2.5 h-2.5 rounded-full bg-amber-400 shrink-0" />
        <div>
          <p className="text-amber-400 font-semibold text-sm">Connect Zerodha Kite</p>
          <p className="text-muted text-xs mt-0.5">
            Link your Demat account to track real holdings alongside paper trades.
          </p>
        </div>
      </div>
      {status.credentials_configured ? (
        <button
          onClick={onConnect}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-gradient-to-r from-blue-600 to-cyan-600 text-white hover:opacity-90 transition-all"
        >
          <ExternalLink size={13} />
          Login with Kite
        </button>
      ) : (
        <p className="text-muted text-xs">Set KITE_API_KEY + KITE_API_SECRET in .env</p>
      )}
    </div>
  );
}

// ── Summary Cards ─────────────────────────────────────────────────────────────

function SummaryCards({ summary }) {
  if (!summary) return null;
  const { total_holdings, total_invested, total_current_value, total_pnl, total_pnl_pct } = summary;
  const gainColor = total_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400';

  const cards = [
    { label: 'Holdings', value: total_holdings, icon: Briefcase, unit: '' },
    { label: 'Invested', value: `₹${fmt(total_invested)}`, icon: IndianRupee, unit: '' },
    { label: 'Current Value', value: `₹${fmt(total_current_value)}`, icon: TrendingUp, unit: '' },
    { label: 'Total P&L', value: `₹${fmt(Math.abs(total_pnl))}`, pct: total_pnl_pct, icon: Percent, unit: '', gainColor },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map(({ label, value, pct: p, icon: Icon, gainColor: gc }) => (
        <div key={label} className="card p-4">
          <div className="flex items-center gap-2 mb-2">
            <Icon size={14} className="text-cyan" />
            <span className="text-muted text-xs">{label}</span>
          </div>
          <p className={`text-xl font-bold ${gc || 'text-slate-100'}`}>{value}</p>
          {p != null && (
            <p className={`text-xs mt-0.5 ${gc}`}>{pct(p)}</p>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Holdings Table ────────────────────────────────────────────────────────────

function HoldingsTable({ holdings }) {
  if (!holdings || holdings.length === 0) {
    return (
      <div className="card p-10 text-center">
        <Briefcase size={32} className="mx-auto text-muted mb-3" />
        <p className="text-muted text-sm">No holdings synced yet.</p>
        <p className="text-muted/60 text-xs mt-1">Connect Kite or add holdings manually.</p>
      </div>
    );
  }

  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
            <th className="px-4 py-3 text-left">Symbol</th>
            <th className="px-4 py-3 text-right">Qty</th>
            <th className="px-4 py-3 text-right">Avg Price</th>
            <th className="px-4 py-3 text-right">LTP</th>
            <th className="px-4 py-3 text-right">Current Value</th>
            <th className="px-4 py-3 text-right">P&L</th>
            <th className="px-4 py-3 text-right">Day Change</th>
            <th className="px-4 py-3 text-right">XIRR</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {holdings.map((h) => (
            <tr key={h.id} className="hover:bg-white/[0.02] transition-colors">
              <td className="px-4 py-3">
                <div>
                  <span className="font-semibold text-slate-200">{h.tradingsymbol}</span>
                  <span className="ml-2 text-[10px] text-muted border border-border/60 px-1 rounded">
                    {h.exchange}
                  </span>
                </div>
                {h.sector && <div className="text-[11px] text-muted mt-0.5">{h.sector}</div>}
              </td>
              <td className="px-4 py-3 text-right text-slate-300">{h.quantity}</td>
              <td className="px-4 py-3 text-right text-slate-300">₹{fmt(h.avg_price)}</td>
              <td className="px-4 py-3 text-right font-medium text-slate-100">₹{fmt(h.last_price)}</td>
              <td className="px-4 py-3 text-right text-slate-200">₹{fmt(h.current_value)}</td>
              <td className="px-4 py-3 text-right">
                <div className="space-y-0.5">
                  <PnlBadge value={h.pnl} />
                  <div><PctBadge value={h.pnl_pct} /></div>
                </div>
              </td>
              <td className="px-4 py-3 text-right">
                <PctBadge value={h.day_change_pct} />
              </td>
              <td className="px-4 py-3 text-right">
                {h.xirr != null ? <PctBadge value={h.xirr} /> : <span className="text-muted text-xs">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Manual Add Form ───────────────────────────────────────────────────────────

function ManualHoldingForm({ onAdded }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    tradingsymbol: '', exchange: 'NSE', quantity: '', avg_price: '',
    last_price: '', sector: '', buy_date: '',
  });
  const [saving, setSaving] = useState(false);

  const handle = (k) => (e) => setForm((p) => ({ ...p, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    if (!form.tradingsymbol || !form.quantity || !form.avg_price) {
      toast.error('Symbol, quantity and avg price are required');
      return;
    }
    setSaving(true);
    try {
      await addManualHolding({
        ...form,
        quantity:  parseInt(form.quantity, 10),
        avg_price: parseFloat(form.avg_price),
        last_price: form.last_price ? parseFloat(form.last_price) : parseFloat(form.avg_price),
      });
      toast.success(`${form.tradingsymbol} added`);
      setForm({ tradingsymbol: '', exchange: 'NSE', quantity: '', avg_price: '', last_price: '', sector: '', buy_date: '' });
      setOpen(false);
      onAdded?.();
    } catch {
      toast.error('Failed to add holding');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <button
        onClick={() => setOpen((p) => !p)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/10 text-sm text-slate-300 hover:text-white hover:bg-white/5 transition-all"
      >
        <Plus size={14} />
        Add Manually
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <form
            onSubmit={submit}
            className="card w-full max-w-md p-6 space-y-4"
          >
            <h3 className="font-bold text-slate-100 text-base">Add Holding</h3>

            <div className="grid grid-cols-2 gap-3">
              {[
                { key: 'tradingsymbol', label: 'Symbol', placeholder: 'RELIANCE' },
                { key: 'exchange', label: 'Exchange', placeholder: 'NSE' },
                { key: 'quantity', label: 'Quantity', placeholder: '10', type: 'number' },
                { key: 'avg_price', label: 'Avg Price (₹)', placeholder: '2500.00', type: 'number' },
                { key: 'last_price', label: 'Last Price (₹)', placeholder: 'optional', type: 'number' },
                { key: 'sector', label: 'Sector', placeholder: 'Energy' },
                { key: 'buy_date', label: 'Buy Date', placeholder: '', type: 'date' },
              ].map(({ key, label, placeholder, type = 'text' }) => (
                <div key={key} className={key === 'buy_date' || key === 'sector' ? 'col-span-2' : ''}>
                  <label className="block text-xs text-muted mb-1">{label}</label>
                  <input
                    type={type}
                    value={form[key]}
                    onChange={handle(key)}
                    placeholder={placeholder}
                    step={type === 'number' ? 'any' : undefined}
                    className="w-full bg-white/5 border border-border rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-muted focus:outline-none focus:border-cyan/40"
                  />
                </div>
              ))}
            </div>

            <div className="flex gap-3 pt-1">
              <button type="submit" disabled={saving}
                className="flex-1 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-cyan-600 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-all">
                {saving ? 'Saving…' : 'Add Holding'}
              </button>
              <button type="button" onClick={() => setOpen(false)}
                className="flex-1 py-2 rounded-lg border border-white/10 text-sm text-slate-300 hover:bg-white/5 transition-all">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Portfolio() {
  const [searchParams] = useSearchParams();

  const [status,   setStatus]   = useState(null);
  const [holdings, setHoldings] = useState({ summary: null, holdings: [] });
  const [loading,  setLoading]  = useState(true);
  const [syncing,  setSyncing]  = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([getKiteStatus(), getKiteHoldings()]);
      setStatus(s);
      setHoldings(h);
    } catch {
      // status fetch fails if credentials not set — still show holdings
      try { setHoldings(await getKiteHoldings()); } catch { /* no holdings */ }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    if (searchParams.get('kite_connected')) {
      toast.success('Zerodha Kite connected! Holdings syncing…');
    }
    const id = setInterval(fetchAll, 30_000);
    return () => clearInterval(id);
  }, [fetchAll, searchParams]);

  async function handleConnect() {
    try {
      const { login_url } = await getKiteLoginUrl();
      window.open(login_url, '_blank', 'noopener');
    } catch {
      toast.error('Could not fetch login URL');
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const res = await syncKiteHoldings();
      toast.success(`Synced ${res.holdings_synced} holdings`);
      await fetchAll();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Sync failed');
    } finally {
      setSyncing(false);
    }
  }

  async function handleDisconnect() {
    try {
      await disconnectKite();
      toast.success('Disconnected from Kite');
      await fetchAll();
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
            <Briefcase size={22} className="text-cyan" />
            My Portfolio
          </h1>
          <p className="text-muted text-sm mt-0.5">
            Real Demat holdings tracker — read-only, no orders placed
          </p>
        </div>
        <ManualHoldingForm onAdded={fetchAll} />
      </div>

      {/* Connection Banner */}
      <ConnectionBanner
        status={status}
        onConnect={handleConnect}
        onSync={handleSync}
        onDisconnect={handleDisconnect}
        syncing={syncing}
      />

      {/* Summary Cards */}
      <SummaryCards summary={holdings.summary} />

      {/* Holdings Table */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold text-slate-200">Holdings</h2>
          <span className="text-xs text-muted">
            {holdings.holdings?.length ?? 0} positions
          </span>
        </div>
        <HoldingsTable holdings={holdings.holdings} />
      </div>

    </div>
  );
}
