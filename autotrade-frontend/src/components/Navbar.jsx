import { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { AlertTriangle, TrendingUp, TrendingDown, Zap, Search, LogOut } from 'lucide-react';
import { usePortfolio } from '../hooks/usePortfolio';
import { getZerodhaTokenStatus, getZerodhaLoginUrl, apiFetch } from '../api/client';
import ExpiryCountdown from './calendar/ExpiryCountdown';
import { useAuth } from '../contexts/AuthContext';

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
  '/buyback':           'Buyback Tracker',
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
  const balance  = portfolio.equity ?? portfolio.balance ?? 0;
  const pnl      = (portfolio.realised_pnl ?? 0) + (portfolio.unrealised_pnl ?? 0);
  const pct      = portfolio.roi_percent ?? 0;
  const positive = pnl >= 0;
  // Compact rupee format for the mobile balance (e.g. ₹5.00L)
  const short = (n) => {
    const a = Math.abs(n);
    if (a >= 1e7) return '₹' + (a / 1e7).toFixed(2) + 'Cr';
    if (a >= 1e5) return '₹' + (a / 1e5).toFixed(2) + 'L';
    return '₹' + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(a);
  };

  return (
    <div className="flex items-center gap-2 md:gap-3 min-w-0">
      <div className="text-right min-w-0">
        {/* Full precision on desktop, compact on mobile */}
        <p className="text-slate-100 font-bold text-sm md:text-base tabular-nums leading-none truncate">
          <span className="hidden sm:inline">
            {'₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(balance)}
          </span>
          <span className="sm:hidden">{short(balance)}</span>
        </p>
        <p className={`text-[10px] md:text-xs font-semibold tabular-nums mt-0.5 ${positive ? 'text-profit' : 'text-loss'}`}>
          {positive ? '+' : ''}{pct.toFixed(2)}%
          <span className="hidden md:inline">
            {' '}({positive ? '+' : ''}{'₹' + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(pnl)})
          </span>
        </p>
      </div>
      {positive
        ? <TrendingUp  size={18} className="text-profit shrink-0" />
        : <TrendingDown size={18} className="text-loss shrink-0" />}
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

  if (!token) return null;

  const expired = !token.valid;
  const nearExpiry = token.valid && token.hours_remaining <= 1;
  if (!expired && !nearExpiry) return null;

  const mins = token.valid ? Math.round(token.hours_remaining * 60) : 0;

  async function handleClick() {
    try {
      const { url } = await getZerodhaLoginUrl();
      window.open(url, '_blank', 'noopener');
    } catch { /* ignore */ }
  }

  if (expired) {
    return (
      <button
        onClick={handleClick}
        className="flex items-center gap-1.5 px-3 py-1 rounded-lg border border-red-500/50 bg-red-500/10 text-red-400 text-xs font-semibold hover:bg-red-500/20 transition-all animate-pulse"
      >
        <Zap size={12} />
        Zerodha token expired — click to refresh
      </button>
    );
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
      const r = await apiFetch('/api/v1/settings/mode');
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
      try {
        await apiFetch('/api/v1/settings/mode', {
          method: 'POST',
          body:   JSON.stringify({ paper_mode: true }),
        });
        load();
      } catch { /* swallow — load() will refresh state */ }
      return;
    }
    // Going LIVE — double confirm
    if (!confirm('Switch to LIVE mode? Real orders will be placed on Zerodha with REAL money.')) return;
    if (!confirm('Are you absolutely sure? Type-confirm in next prompt is locked.')) return;
    try {
      await apiFetch('/api/v1/settings/mode', {
        method: 'POST',
        body:   JSON.stringify({ paper_mode: false, confirm: 'I_UNDERSTAND_REAL_MONEY' }),
      });
    } catch (e) {
      alert('Could not switch to LIVE: ' + (e.message || 'unknown error'));
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

export default function Navbar({ onSearchOpen }) {
  const { pathname } = useLocation();
  const { portfolio } = usePortfolio();
  const { logout } = useAuth();
  const title = PAGE_TITLES[pathname] ?? 'AutoTrade Pro';

  return (
    <header className="shrink-0 border-b border-white/5 bg-black/40 backdrop-blur-md relative z-40">
      {/* Disclaimer */}
      <div className="flex items-center justify-center gap-2 px-4 py-1.5 border-b border-warn/15 bg-amber-500/5">
        <AlertTriangle size={11} className="text-warn/70 shrink-0" />
        <span className="text-warn/70 text-[11px] font-semibold tracking-wider uppercase">
          Paper Trading Only — Simulated Money — No Real Trades
        </span>
        <AlertTriangle size={11} className="text-warn/70 shrink-0" />
      </div>

      {/* Main row */}
      <div className="flex items-center justify-between gap-2 md:gap-3 px-4 md:px-6 py-3">
        <div className="flex items-center gap-2 md:gap-3 shrink min-w-0">
          <h1 className="text-slate-100 font-bold text-base md:text-lg truncate">{title}</h1>
          <div className="shrink-0"><TradeModeBadge /></div>
        </div>

        {/* ⌘K Search trigger (desktop) — shrinks before the title does */}
        {onSearchOpen && (
          <button
            onClick={onSearchOpen}
            className="hidden md:flex items-center gap-2 flex-1 min-w-0 max-w-sm mx-2 lg:mx-6 bg-white/5 border border-white/10 hover:border-accent/40 rounded-lg px-3 h-9 text-slate-400 hover:text-slate-200 hover:bg-white/10 text-sm transition-all duration-300 shadow-[0_2px_10px_rgba(0,0,0,0.2)]"
          >
            <Search size={14} className="shrink-0" />
            <span className="flex-1 text-left truncate">Search any stock, MF…</span>
            <kbd className="hidden lg:inline text-[10px] font-mono bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-muted shrink-0">⌘K</kbd>
          </button>
        )}

        <div className="flex items-center gap-2 md:gap-4 lg:gap-5 shrink-0">
          {/* Mobile search icon */}
          {onSearchOpen && (
            <button onClick={onSearchOpen} className="md:hidden text-muted hover:text-slate-300 p-1" aria-label="Search">
              <Search size={18} />
            </button>
          )}
          {/* Token warning — always visible when expired, hidden on xs when near-expiry only */}
          <ZerodhaTokenBanner />
          {/* Expiry countdown — desktop only */}
          <div className="hidden xl:block"><ExpiryCountdown /></div>
          {/* Market status dots — large screens */}
          <div className="hidden lg:flex"><MarketStatusDots /></div>
          <div className="hidden lg:block w-px h-8 bg-border" />
          {/* Balance — always visible (core info), compact on mobile */}
          <BalanceTicker portfolio={portfolio} />
          {/* Live clock — tablet and up */}
          <div className="hidden md:block w-px h-8 bg-border" />
          <div className="hidden md:block"><LiveClock /></div>
          {/* Logout */}
          <div className="hidden md:block w-px h-8 bg-border" />
          <button
            onClick={logout}
            title="Sign out"
            className="flex items-center gap-1.5 text-slate-400 hover:text-red-400 transition text-xs font-medium px-2 py-1.5 rounded-lg hover:bg-red-500/10"
          >
            <LogOut size={14} />
            <span className="hidden lg:inline">Sign out</span>
          </button>
        </div>
      </div>
    </header>
  );
}
