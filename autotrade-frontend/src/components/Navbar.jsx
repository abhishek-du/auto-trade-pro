import { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { AlertTriangle, TrendingUp, TrendingDown, Zap } from 'lucide-react';
import { usePortfolio } from '../hooks/usePortfolio';
import { getZerodhaTokenStatus, getZerodhaLoginUrl } from '../api/client';
import ExpiryCountdown from './calendar/ExpiryCountdown';

const PAGE_TITLES = {
  '/':                 'Dashboard',
  '/trades':           'Trades',
  '/analytics':        'Analytics',
  '/news':             'News Feed',
  '/simulation':       'Simulation',
  '/settings':         'Settings',
  '/documentation':    'Documentation',
  '/india':            'India Overview',
  '/india/signals':    'NSE Signals',
  '/mutual-funds':     'Mutual Funds',
  '/fundamentals':     'Fundamentals',
  '/backtest':         'Backtest',
  '/portfolio':        'Simulator',
  '/zerodha':          'Zerodha KiteConnect',
  '/calendar':          'Market Calendar',
  '/portfolio-tracker': 'My Portfolio',
  '/doctor':            'Portfolio Doctor',
  '/earnings':          'Earnings Analyzer',
  '/agent':             'AI Trading Agent',
  '/intelligence':      'Intelligence Hub',
};

// ── Market status dots ────────────────────────────────────────────────────────

function MarketStatusDots() {
  const [s, setS] = useState({ nseOpen: false, nyseOpen: false, ist: '', et: '' });

  useEffect(() => {
    const tick = () => {
      const now = new Date();

      // NSE: Mon–Fri 9:15–15:30 IST (Asia/Kolkata)
      const istParts = Object.fromEntries(
        new Intl.DateTimeFormat('en-US', {
          timeZone: 'Asia/Kolkata',
          weekday: 'short', hour: 'numeric', minute: 'numeric', hour12: false,
        }).formatToParts(now).map(p => [p.type, p.value])
      );
      const ih = +istParts.hour, im = +istParts.minute;
      const nseOpen =
        !['Sat', 'Sun'].includes(istParts.weekday) &&
        ((ih > 9) || (ih === 9 && im >= 15)) &&
        ((ih < 15) || (ih === 15 && im <= 30));
      const ist = `${String(ih).padStart(2, '0')}:${String(im).padStart(2, '0')}`;

      // NYSE: Mon–Fri 9:30–16:00 ET (America/New_York handles DST)
      const etParts = Object.fromEntries(
        new Intl.DateTimeFormat('en-US', {
          timeZone: 'America/New_York',
          weekday: 'short', hour: 'numeric', minute: 'numeric',
          hour12: false, timeZoneName: 'short',
        }).formatToParts(now).map(p => [p.type, p.value])
      );
      const eh = +etParts.hour, em = +etParts.minute;
      const nyseOpen =
        !['Sat', 'Sun'].includes(etParts.weekday) &&
        ((eh > 9) || (eh === 9 && em >= 30)) &&
        eh < 16;
      const et = `${String(eh).padStart(2, '0')}:${String(em).padStart(2, '0')} ${etParts.timeZoneName ?? 'ET'}`;

      setS({ nseOpen, nyseOpen, ist, et });
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.nseOpen ? 'bg-profit' : 'bg-loss'}`} />
        <span className="text-muted text-xs font-mono">NSE {s.ist}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.nyseOpen ? 'bg-profit' : 'bg-loss'}`} />
        <span className="text-muted text-xs font-mono">{s.et}</span>
      </div>
    </div>
  );
}

// ── Live clock ────────────────────────────────────────────────────────────────

function LiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="text-right">
      <p className="text-slate-200 font-mono text-sm tabular-nums">
        {time.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </p>
      <p className="text-muted text-[10px]">
        {time.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
      </p>
    </div>
  );
}

// ── Balance ticker ────────────────────────────────────────────────────────────

function BalanceTicker({ portfolio }) {
  if (!portfolio) return <span className="text-muted text-sm">Loading…</span>;
  const balance  = portfolio.balance ?? 0;
  const pnl      = (portfolio.realised_pnl ?? 0) + (portfolio.unrealised_pnl ?? 0);
  const pct      = portfolio.roi_percent ?? 0;
  const positive = pnl >= 0;
  return (
    <div className="flex items-center gap-3">
      <div className="text-right">
        <p className="text-slate-100 font-bold text-base tabular-nums leading-none">
          {'₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(balance)}
        </p>
        <p className={`text-xs font-semibold tabular-nums mt-0.5 ${positive ? 'text-profit' : 'text-loss'}`}>
          {positive ? '+' : ''}
          {'₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(pnl)}
          {' '}({positive ? '+' : ''}{pct.toFixed(2)}%)
        </p>
      </div>
      {positive
        ? <TrendingUp  size={18} className="text-profit" />
        : <TrendingDown size={18} className="text-loss" />}
    </div>
  );
}

// ── Zerodha token expiry warning ──────────────────────────────────────────────

function ZerodhaTokenBanner() {
  const [token, setToken] = useState(null);

  useEffect(() => {
    const check = () =>
      getZerodhaTokenStatus()
        .then(setToken)
        .catch(() => setToken(null));
    check();
    const id = setInterval(check, 5 * 60 * 1000); // every 5 min
    return () => clearInterval(id);
  }, []);

  // Only show warning if token valid but expires within 60 min
  if (!token?.valid || token.hours_remaining > 1) return null;

  const mins = Math.round(token.hours_remaining * 60);

  async function handleClick() {
    try {
      const { url } = await getZerodhaLoginUrl();
      window.open(url, '_blank', 'noopener');
    } catch { /* ignore */ }
  }

  return (
    <button
      onClick={handleClick}
      className="flex items-center gap-1.5 px-3 py-1 rounded-lg border border-amber-500/30 text-amber-400 text-xs font-semibold hover:bg-amber-500/10 transition-all"
    >
      <Zap size={12} />
      Zerodha token expires in {mins} min — re-login
    </button>
  );
}

// ── Trade mode badge (PAPER ↔ LIVE toggle) ───────────────────────────────────

function TradeModeBadge() {
  const [mode, setMode] = useState(null);

  async function load() {
    try {
      const r = await fetch('/api/v1/settings/mode');
      if (r.ok) setMode(await r.json());
    } catch {}
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  async function toggle() {
    if (!mode) return;
    if (mode.is_live) {
      // Switch back to paper — no confirm needed
      const r = await fetch('/api/v1/settings/mode', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paper_mode: true }),
      });
      if (r.ok) load();
      return;
    }
    // Going LIVE — double confirm
    if (!confirm('Switch to LIVE mode? Real orders will be placed on Zerodha with REAL money.')) return;
    if (!confirm('Are you absolutely sure? Type-confirm in next prompt is locked.')) return;
    const r = await fetch('/api/v1/settings/mode', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paper_mode: false, confirm: 'I_UNDERSTAND_REAL_MONEY' }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('Could not switch to LIVE: ' + (err.detail || 'unknown error'));
    }
    load();
  }

  if (!mode) return null;

  const cls = mode.is_live
    ? 'bg-red-500/15 text-red-400 border-red-500/30 animate-pulse'
    : mode.is_dry_run
      ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
      : 'bg-blue-500/15 text-blue-400 border-blue-500/30';
  return (
    <button onClick={toggle}
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[10px] font-bold uppercase tracking-widest ${cls}`}
      title={`Current mode: ${mode.mode}. Click to toggle.`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />
      {mode.mode}
    </button>
  );
}

// ── Navbar ────────────────────────────────────────────────────────────────────

export default function Navbar() {
  const { pathname } = useLocation();
  const { portfolio } = usePortfolio();
  const title = PAGE_TITLES[pathname] ?? 'AutoTrade Pro';

  return (
    <header className="shrink-0 border-b border-border" style={{ background: '#0A1120' }}>
      {/* Disclaimer */}
      <div className="flex items-center justify-center gap-2 px-4 py-1.5 border-b border-warn/15"
        style={{ background: 'rgba(245,158,11,0.05)' }}>
        <AlertTriangle size={11} className="text-warn/70 shrink-0" />
        <span className="text-warn/70 text-[11px] font-semibold tracking-wider uppercase">
          Paper Trading Only — Simulated Money — No Real Trades
        </span>
        <AlertTriangle size={11} className="text-warn/70 shrink-0" />
      </div>

      {/* Main row */}
      <div className="flex items-center justify-between px-6 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-slate-100 font-bold text-lg">{title}</h1>
          <TradeModeBadge />
        </div>
        <div className="flex items-center gap-5">
          <ZerodhaTokenBanner />
          <ExpiryCountdown />
          <MarketStatusDots />
          <div className="w-px h-8 bg-border" />
          <BalanceTicker portfolio={portfolio} />
          <div className="w-px h-8 bg-border" />
          <LiveClock />
        </div>
      </div>
    </header>
  );
}
