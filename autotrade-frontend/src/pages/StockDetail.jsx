/**
 * StockDetail — /s/:symbol
 * Decision-first unified stock page (Phase 2).
 *
 * Section order:
 *   1  Decision Center   (verdict · trade plan · ₹10k card · confidence breakdown · scenarios)
 *   2  What Changed Today (promoted news feed, timestamped)
 *   3  Intelligence Snapshot (7 chips with dot-meter)
 *   4  Chart (CandlestickChart + indicator strip)
 *   5  AI Research Report (executive summary · bull/bear/risks · why now · entry/exit · suitability)
 *   6  News + Timeline merged
 *   ↓  Progressive disclosure line
 *   7  Deep tabs (Company · Financials · Ownership · Peers · Technicals · Options · Compare)
 */
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  ArrowLeft, Star, Bell, Share2, TrendingUp, TrendingDown,
  ShieldAlert, ChevronDown, RefreshCw, IndianRupee,
  Zap, BookOpen, BarChart2, Users, PieChart, Activity,
} from 'lucide-react';
import { apiFetch } from '../api/client';
import CandlestickChart from '../components/chart/CandlestickChart';

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (n, d = 2) =>
  n == null || isNaN(n) ? '—'
  : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

const pct = (n) =>
  n == null || isNaN(n) ? '—'
  : (n >= 0 ? '+' : '') + fmt(n) + '%';

const rupee = (n) =>
  n == null || isNaN(n) ? '—'
  : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });

function Skel({ w = 'w-24', h = 'h-4', extra = '' }) {
  return <div className={`${w} ${h} rounded animate-pulse bg-white/5 ${extra}`} />;
}

function SectionLabel({ color = 'text-cyan', children }) {
  return (
    <div className={`text-[10px] ${color} font-semibold uppercase tracking-widest mb-3 flex items-center gap-2`}>
      {children}
    </div>
  );
}

function SignalChip({ signal }) {
  if (!signal) return null;
  const s = String(signal).toUpperCase().replace('_', ' ');
  const isBuy  = s.includes('BUY');
  const isSell = s.includes('SELL');
  const cls = isBuy  ? 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30'
            : isSell ? 'text-red-400 bg-red-500/15 border-red-500/30'
            :          'text-amber-400 bg-amber-500/15 border-amber-500/30';
  return <span className={`text-xs font-bold px-2 py-0.5 rounded border ${cls}`}>{s === 'STRONG BUY' ? 'BUY' : s === 'STRONG SELL' ? 'SELL' : s}</span>;
}

function DotMeter({ value, max = 5, colorClass = 'text-emerald-400' }) {
  const filled = Math.round(Math.max(0, Math.min(max, (value / 100) * max)));
  return (
    <div className={`flex items-center gap-0.5 ${colorClass}`} style={{ fontSize: 11 }}>
      {Array.from({ length: max }, (_, i) => (
        <span key={i} style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: i < filled ? 'currentColor' : 'rgba(255,255,255,0.1)' }} />
      ))}
    </div>
  );
}

// ── ₹10,000 Scenario Card ─────────────────────────────────────────────────────

function InvestCard({ tradeSetup, ltp, signal }) {
  // Derive % moves from trade setup, falling back to score-based estimates
  const entry = ltp ?? tradeSetup?.entry_high ?? 100;
  const t2    = tradeSetup?.target_2;
  const t1    = tradeSetup?.target_1;
  const sl    = tradeSetup?.stop_loss;

  const isSell = String(signal || '').toUpperCase().includes('SELL');

  let bullPct, basePct, bearPct;

  if (t2 && entry) {
    bullPct = ((t2 - entry) / entry) * 100;
  } else {
    bullPct = isSell ? -12 : 12;
  }
  if (t1 && entry) {
    basePct = ((t1 - entry) / entry) * 100;
  } else {
    basePct = isSell ? -5 : 5;
  }
  if (sl && entry) {
    bearPct = ((sl - entry) / entry) * 100;
  } else {
    bearPct = isSell ? 8 : -8;
  }

  const AMOUNT  = 10_000;
  const bullVal = AMOUNT * (1 + bullPct / 100);
  const baseVal = AMOUNT * (1 + basePct / 100);
  const bearVal = AMOUNT * (1 + bearPct / 100);

  const horizon = tradeSetup?.hold_strategy?.match(/\d+[\s\-]+\d+/) ?
    tradeSetup.hold_strategy.match(/\d+[\s\-]+\d+/)[0] + ' trading days' :
    '5–15 trading days';

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <IndianRupee size={14} className="text-amber-400" />
        <span className="text-slate-200 text-sm font-semibold">If I invest ₹10,000 today</span>
        <span className="ml-auto text-[10px] text-muted">Horizon: {horizon}</span>
      </div>
      <div className="grid grid-cols-3 gap-2.5 text-center">
        <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3">
          <div className="text-emerald-400 text-[10px] font-bold uppercase tracking-wider mb-1 flex items-center justify-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
            Bull · 48%
          </div>
          <div className="font-mono text-emerald-400 text-base font-black">{rupee(bullVal)}</div>
          <div className="text-emerald-400 text-[10px] font-mono mt-0.5">{pct(bullPct)}</div>
          {t2 && <div className="text-muted text-[10px] mt-0.5">Target ₹{fmt(t2)}</div>}
        </div>
        <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3">
          <div className="text-amber-400 text-[10px] font-bold uppercase tracking-wider mb-1 flex items-center justify-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
            Base · 32%
          </div>
          <div className="font-mono text-amber-400 text-base font-black">{rupee(baseVal)}</div>
          <div className="text-amber-400 text-[10px] font-mono mt-0.5">{pct(basePct)}</div>
          {t1 && <div className="text-muted text-[10px] mt-0.5">Target ₹{fmt(t1)}</div>}
        </div>
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
          <div className="text-red-400 text-[10px] font-bold uppercase tracking-wider mb-1 flex items-center justify-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-red-400 shrink-0" />
            Bear · 20%
          </div>
          <div className="font-mono text-red-400 text-base font-black">{rupee(bearVal)}</div>
          <div className="text-red-400 text-[10px] font-mono mt-0.5">{pct(bearPct)}</div>
          {sl && <div className="text-muted text-[10px] mt-0.5">SL ₹{fmt(sl)}</div>}
        </div>
      </div>
      <p className="text-muted text-[10px] mt-2.5 leading-relaxed">
        Projections from AI trade setup · not a guarantee · always use stop loss · position-size to your risk.
      </p>
    </div>
  );
}

// ── Intelligence chip card ─────────────────────────────────────────────────────

function IntelChip({ label, rawScore, note, icon: Icon, noData }) {
  if (noData) {
    return (
      <div className="bg-card border border-border rounded-xl p-4 opacity-60">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            {Icon && <Icon size={12} className="text-muted" />}
            <span className="text-[10px] text-muted uppercase tracking-wider">{label}</span>
          </div>
          <span className="w-2 h-2 rounded-full shrink-0 bg-slate-600" />
        </div>
        <div className="text-base font-bold leading-tight text-slate-500">No data</div>
        <div className="text-[10px] text-muted mt-2 leading-snug">Not in tracked universe</div>
      </div>
    );
  }
  const norm = Math.max(0, Math.min(100, 50 + (rawScore ?? 0)));
  const isPos  = (rawScore ?? 0) >= 10;
  const isNeg  = (rawScore ?? 0) <= -10;
  const color  = isPos ? 'text-emerald-400' : isNeg ? 'text-red-400' : 'text-amber-400';
  const border = isPos ? 'hover:border-emerald-500/40' : isNeg ? 'hover:border-red-500/40' : 'hover:border-amber-500/40';
  const verdict = isPos ? 'Bullish' : isNeg ? 'Bearish' : 'Neutral';
  return (
    <div className={`bg-card border border-border ${border} rounded-xl p-4 transition-colors cursor-pointer`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          {Icon && <Icon size={12} className="text-muted" />}
          <span className="text-[10px] text-muted uppercase tracking-wider">{label}</span>
        </div>
        <span className={`w-2 h-2 rounded-full shrink-0 ${isPos ? 'bg-emerald-400' : isNeg ? 'bg-red-400' : 'bg-amber-400'}`} />
      </div>
      <div className={`text-base font-bold leading-tight ${color}`}>{verdict}</div>
      <DotMeter value={norm} colorClass={color} />
      {note && <div className="text-[10px] text-muted mt-2 leading-snug line-clamp-2">{note}</div>}
    </div>
  );
}

// ── Deep tab accordion ────────────────────────────────────────────────────────

function DeepTab({ label, subtitle, badge, badgeColor = '', icon: Icon, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/[0.03] transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        {Icon && <Icon size={14} className="text-muted shrink-0" />}
        <span className="flex-1 text-left">
          <span className="text-slate-200 text-sm font-medium">{label}</span>
          {subtitle && <span className="text-muted text-xs ml-2">{subtitle}</span>}
        </span>
        {badge && (
          <span className={`text-[10px] font-bold bg-white/5 border border-border px-2 py-0.5 rounded ${badgeColor}`}>{badge}</span>
        )}
        <ChevronDown size={14} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="border-t border-border px-4 py-4 bg-surface/40">{children}</div>}
    </div>
  );
}

// ── News event row ────────────────────────────────────────────────────────────

function NewsRow({ n, divider }) {
  const pos = n.sentiment === 'positive' || n.score > 0;
  const neg = n.sentiment === 'negative' || n.score < 0;
  const dotCls = pos ? 'bg-profit' : neg ? 'bg-loss' : 'bg-slate-500';
  const labelCls = pos ? 'text-profit' : neg ? 'text-loss' : 'text-muted';
  const label    = pos ? 'Bullish' : neg ? 'Bearish' : 'Neutral';
  return (
    <div className={`flex items-start gap-3 px-4 py-3 ${divider ? 'border-t border-border' : ''} hover:bg-white/[0.02]`}>
      <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${dotCls}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className={`text-[10px] font-bold uppercase tracking-wider ${labelCls}`}>{label}</span>
          {n.source && <span className="text-muted text-[10px]">{n.source}</span>}
          {n.published_at && <span className="text-muted text-[10px] font-mono">{n.published_at?.slice(0, 10)}</span>}
        </div>
        <div className="text-slate-200 text-sm leading-snug">{n.headline}</div>
        {n.impact && <div className="text-muted text-[11px] mt-1"><span className={`font-semibold ${labelCls}`}>Impact:</span> {n.impact}</div>}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function StockDetail() {
  const { symbol }  = useParams();
  const navigate    = useNavigate();
  const pollRef     = useRef(null);

  const nsSymbol = (symbol?.endsWith('.NS') || symbol?.endsWith('.BO'))
    ? symbol : symbol + '.NS';
  const display  = symbol?.replace('.NS', '').replace('.BO', '').toUpperCase();

  const [price,   setPrice]   = useState(null);
  const [deep,    setDeep]    = useState(null);
  const [intel,   setIntel]   = useState(null);
  const [fund,    setFund]    = useState(null);
  const [fundLoading, setFundLoading] = useState(true);
  const [deepSettled, setDeepSettled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [autoFloor, setAutoFloor] = useState(30);   // live agent auto-trade threshold
  const [inWatchlist, setInWatchlist] = useState(false);
  const [wlLoading,   setWlLoading]   = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFundLoading(true);
    setDeepSettled(false);
    setError(null);
    try {
      // Critical path: price + hub score only. Keep this fast so the
      // Decision Center paints immediately.
      const [priceR, intelR] = await Promise.allSettled([
        apiFetch(`/api/v1/india/live-prices/${encodeURIComponent(nsSymbol)}`),
        apiFetch(`/api/v1/intelligence/score-breakdown/${encodeURIComponent(nsSymbol)}`),
      ]);
      if (priceR.status === 'fulfilled') setPrice(priceR.value);
      if (intelR.status === 'fulfilled') setIntel(intelR.value);

      // Deep analysis — heavier (indicators + reasoning), non-blocking.
      apiFetch(`/api/v1/zerodha/deep-analysis/${encodeURIComponent(display)}`)
        .then(d => setDeep(d))
        .catch(() => {})
        .finally(() => setDeepSettled(true));

      // Fundamentals — may fetch on-demand (yfinance + Screener, ~5s first
      // hit), so it MUST stay off the critical path. Populates the deep tabs.
      apiFetch(`/api/v1/india/fundamentals/${encodeURIComponent(nsSymbol)}`)
        .then(d => setFund(d))
        .catch(() => {})
        .finally(() => setFundLoading(false));

      // Check if symbol is in user watchlist (non-blocking)
      apiFetch('/api/v1/india/user-watchlist')
        .then(d => setInWatchlist((d.symbols || []).includes(nsSymbol)))
        .catch(() => {});

      // Live agent auto-trade threshold (so the "signal only" floor stays in
      // sync with the backend PAPER_CONFIDENCE_THRESHOLD instead of hardcoding).
      apiFetch('/api/v1/india/market-scanner/shortlist?limit=1')
        .then(d => { if (d?.auto_trade_threshold != null) setAutoFloor(d.auto_trade_threshold); })
        .catch(() => {});
    } catch (e) {
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [nsSymbol, display]);

  const toggleWatchlist = useCallback(async () => {
    setWlLoading(true);
    try {
      if (inWatchlist) {
        await apiFetch(`/api/v1/india/user-watchlist/${encodeURIComponent(display)}`, { method: 'DELETE' });
        setInWatchlist(false);
      } else {
        await apiFetch(`/api/v1/india/user-watchlist/${encodeURIComponent(display)}`, { method: 'POST' });
        setInWatchlist(true);
      }
    } catch (e) {
      // silently ignore
    } finally {
      setWlLoading(false);
    }
  }, [inWatchlist, display]);

  useEffect(() => {
    load();
    // Poll price every 30 s while page is visible
    pollRef.current = setInterval(() => {
      apiFetch(`/api/v1/india/live-prices/${encodeURIComponent(nsSymbol)}`)
        .then(d => setPrice(d))
        .catch(() => {});
    }, 30_000);
    return () => clearInterval(pollRef.current);
  }, [load, nsSymbol]);

  // Whether this symbol is in the Master Intelligence Hub's tracked universe.
  // Untracked symbols (any of ~9,600 NSE stocks reachable via search) have no
  // hub score / fundamentals row — we fall back to on-the-fly deep-analysis,
  // which works for every symbol.
  const isTracked  = !!intel;
  const signal     = intel?.signal ?? deep?.signal ?? price?.signal ?? null;
  const score      = intel?.master_score ?? deep?.composite_score ?? null;
  const comp       = intel?.components ?? {};
  const reasoning  = intel?.full_reasoning ?? {};
  const ts         = deep?.trade_setup ?? {};
  const indicators = deep?.indicators ?? {};
  const news       = deep?.news ?? [];
  const ltp        = deep?.ltp ?? price?.price ?? null;

  const isBuy  = String(signal || '').includes('BUY');
  const isSell = String(signal || '').includes('SELL');
  const signalColor = isBuy ? 'text-profit' : isSell ? 'text-loss' : 'text-amber-400';
  const signalGlow  = isBuy ? 'rgba(16,185,129,0.3)' : isSell ? 'rgba(239,68,68,0.3)' : 'rgba(59,130,246,0.3)';

  // Confidence = conviction in the *direction* = distance from neutral = |score|.
  // This matches the backend convention exactly (india_signal_generator and
  // risk_manager both use confidence == abs(score), e.g. score -21.3 → conf 21%).
  // The earlier `50 + score/2` mapping was wrong: it made a strong SELL look
  // low-confidence and a neutral score look 50%-confident.
  const conf = score != null ? Math.min(100, Math.round(Math.abs(score))) : null;

  // The agent only auto-trades signals at/above this confidence (live value from
  // the backend PAPER_CONFIDENCE_THRESHOLD). Below it, a BUY/SELL is suggestion only.
  const AUTO_TRADE_FLOOR = autoFloor;

  // Conviction label aligned to the agent's trade threshold: a setup the agent
  // would actually act on (≥ the auto-trade floor) is at least MEDIUM.
  const convictionLabel = conf == null ? '—' : conf >= 60 ? 'HIGH' : conf >= AUTO_TRADE_FLOOR ? 'MEDIUM' : 'LOW';

  // Verdict is pending while the fast batch is in flight, OR while we have no
  // hub score yet and deep-analysis hasn't settled. Once deep settles (success
  // or 404), we stop waiting — preventing an infinite skeleton for delisted /
  // dataless symbols (e.g. a ticker removed after a demerger).
  const verdictPending = loading || (!intel && !deep && !deepSettled);

  // True when every analysis source failed — show an honest terminal message
  // instead of empty cards (e.g. TATAMOTORS.NS after the Tata Motors demerger).
  const noData = !verdictPending && !intel && !deep && !price;

  return (
    <div className="-m-6 flex flex-col min-h-screen" style={{ background: '#080D1A' }}>

      {/* ── Sticky symbol header ─────────────────────────────────────── */}
      <div className="sticky top-0 z-30 border-b border-border px-5 py-3 flex items-center gap-3"
        style={{ background: 'rgba(8,13,26,0.97)', backdropFilter: 'blur(12px)' }}>
        <button onClick={() => navigate(-1)} className="text-muted hover:text-slate-300 p-1 rounded-lg hover:bg-white/5">
          <ArrowLeft size={16} />
        </button>
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-900 to-blue-600 grid place-items-center font-bold text-white text-sm shrink-0">
          {display?.[0] ?? '?'}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-100 font-semibold text-base">{fund?.company_name || price?.name || display}</span>
            <span className="font-mono text-[10px] bg-white/5 border border-border px-1.5 py-0.5 rounded text-muted">{display}</span>
            <span className="text-[10px] bg-white/5 border border-border px-1.5 py-0.5 rounded text-muted">NSE · EQ</span>
            {isTracked ? (
              <span className="text-[10px] font-bold text-violet-300 bg-violet-500/10 border border-violet-500/30 px-1.5 py-0.5 rounded"
                title="Deep-scored by the Hub: technical + news + fundamentals + earnings + sector + macro + options">
                HUB 7-FACTOR
              </span>
            ) : !verdictPending && (
              <span className="text-[10px] font-semibold text-amber-400/90 bg-amber-500/10 border border-amber-500/25 px-1.5 py-0.5 rounded"
                title="Not in the Hub's auto-trade universe — technical analysis only, agent will not trade this">
                NOT IN AUTO-TRADE UNIVERSE
              </span>
            )}
            {signal && <SignalChip signal={signal} />}
          </div>
          <div className="text-muted text-xs mt-0.5 truncate">
            {fund ? `${fund.sector || 'NSE Equity'}${fund.market_cap_cr ? ` · ₹${fmt(fund.market_cap_cr, 0)} Cr mkt cap` : ''}` : 'NSE Equity'}
          </div>
        </div>
        <div className="flex items-baseline gap-2 shrink-0">
          {loading && !price ? <Skel w="w-28" h="h-8" /> : (
            <>
              <span className="font-mono text-2xl font-bold text-slate-100">{ltp ? `₹${fmt(ltp)}` : price?.price ? `₹${fmt(price.price)}` : '—'}</span>
              {price?.change_pct != null && (
                <span className={`font-mono text-sm font-semibold ${price.change_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {pct(price.change_pct)}
                </span>
              )}
            </>
          )}
        </div>
        <div className="flex items-center gap-1 ml-1 shrink-0">
          <button onClick={toggleWatchlist} disabled={wlLoading} className={`p-1.5 rounded-lg hover:bg-white/5 transition-colors ${inWatchlist ? 'text-amber-400' : 'text-muted hover:text-amber-400'}`} title={inWatchlist ? 'Remove from watchlist' : 'Add to watchlist'}><Star size={15} className={inWatchlist ? 'fill-amber-400' : ''} /></button>
          <button className="text-muted hover:text-cyan p-1.5 rounded-lg hover:bg-white/5 transition-colors" title="Alert"><Bell size={15} /></button>
          <button className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5 transition-colors" title="Share"><Share2 size={15} /></button>
          <button onClick={load} className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5" title="Refresh">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Price strip */}
      {(price || deep) && (
        <div className="px-5 py-2.5 border-b border-border flex items-center gap-6 text-xs flex-wrap"
          style={{ background: '#0A1120' }}>
          {[
            ['Open',    price?.open],
            ['High',    price?.high],
            ['Low',     price?.low],
            ['Volume',  price?.volume ? (price.volume > 1e6 ? fmt(price.volume / 1e6, 1) + 'M' : fmt(price.volume, 0)) : null],
            ['52W H',   price?.week52High ?? price?.['52w_high']],
            ['52W L',   price?.week52Low  ?? price?.['52w_low']],
          ].filter(([, v]) => v != null && v !== 0).map(([k, v]) => (
            <div key={k} className="flex gap-1.5 items-baseline">
              <span className="text-muted">{k}</span>
              <span className="font-mono text-slate-200">{typeof v === 'string' ? v : fmt(v)}</span>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="mx-5 mt-4 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 text-sm">{error}</div>
      )}

      {/* Terminal no-data state — every analysis source returned nothing
          (e.g. a delisted / renamed ticker with no price, score, or candles). */}
      {noData && (
        <div className="flex-1 grid place-items-center px-5 py-20">
          <div className="text-center max-w-md">
            <div className="w-14 h-14 rounded-2xl bg-white/5 border border-border grid place-items-center mx-auto mb-4">
              <ShieldAlert size={24} className="text-muted" />
            </div>
            <h3 className="text-slate-200 text-lg font-semibold mb-2">No data for {display}</h3>
            <p className="text-muted text-sm leading-relaxed">
              We couldn't find live price, candles, or an AI score for this symbol.
              It may be delisted, renamed (e.g. after a demerger), or not yet covered.
              Try searching for the current ticker.
            </p>
            <button onClick={load} className="mt-5 inline-flex items-center gap-2 bg-white/[0.04] hover:bg-white/[0.07] border border-border text-slate-300 rounded-lg px-4 py-2 text-sm transition-colors">
              <RefreshCw size={14} /> Retry
            </button>
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 1 — DECISION CENTER
      ═══════════════════════════════════════════════════════════════ */}
      {!noData && <>
      <section className="px-5 pt-6 pb-6" style={{ background: 'linear-gradient(180deg,#0A1120 0%,#080D1A 100%)' }}>
        <SectionLabel color="text-cyan">
          Section 1 · Decision center
          <span className="ml-auto text-[10px] text-muted font-normal normal-case tracking-normal flex items-center gap-2">
            {!verdictPending && !isTracked && (
              <span className="text-amber-400/90 normal-case" title="Not in the Master Intelligence tracked universe — using on-the-fly technical analysis">
                On-the-fly analysis
              </span>
            )}
            <span>
              Updated {new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })} IST
              {reasoning.regime ? <> · Regime: <span className="text-slate-300">{reasoning.regime}</span></> : null}
            </span>
          </span>
        </SectionLabel>

        {/* Action banner */}
        <div className="rounded-2xl border border-border p-5 mb-3 relative overflow-hidden"
          style={{ background: 'linear-gradient(145deg,#131E30,#0F1829)', boxShadow: `0 0 40px -15px ${signalGlow}` }}>
          <div className="absolute -right-20 -top-20 w-80 h-80 rounded-full blur-3xl opacity-15"
            style={{ background: signalGlow }} />

          <div className="relative grid grid-cols-1 lg:grid-cols-12 gap-5">
            {/* Verdict col */}
            <div className="lg:col-span-3 lg:border-r lg:border-border lg:pr-5">
              <div className="text-muted text-[10px] uppercase tracking-widest mb-2">AI Action</div>
              {verdictPending ? (
                <div className="space-y-2"><Skel w="w-32" h="h-12" /><Skel w="w-24" h="h-3" /></div>
              ) : (
                <>
                  <div className={`text-5xl font-black tracking-tight leading-none ${signalColor}`}>
                    {isBuy ? 'BUY' : isSell ? 'SELL' : 'HOLD'}
                    {String(signal||'').startsWith('STRONG') && (
                      <span className="text-xl ml-1 align-top mt-1 inline-block">NOW</span>
                    )}
                  </div>
                  <div className="text-slate-400 text-xs mt-2">
                    {ts.when_to_buy?.split('\n')[0]?.replace(/\*\*/g, '').trim().slice(0, 80) || ''}
                  </div>

                  {/* Confidence + conviction + risk */}
                  <div className="mt-3 space-y-2">
                    {conf != null && (
                      <div className="flex items-center gap-2">
                        <span className="text-muted text-[10px]">Confidence</span>
                        <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                          <div className="h-full rounded-full bg-gradient-to-r from-cyan to-profit"
                            style={{ width: `${conf}%` }} />
                        </div>
                        <span className="font-mono text-cyan text-sm font-bold">{conf}%</span>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="text-muted text-[10px] w-20">Conviction</span>
                      <DotMeter
                        value={conf ?? 0}
                        colorClass={isBuy ? 'text-emerald-400' : isSell ? 'text-red-400' : 'text-amber-400'}
                      />
                      <span className={`text-[11px] font-bold ${convictionLabel === 'HIGH' ? (isBuy ? 'text-profit' : 'text-loss') : 'text-amber-400'}`}>
                        {convictionLabel}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-muted text-[10px] w-20">Risk</span>
                      <span className={`chip text-[10px] font-bold px-1.5 py-0.5 rounded border ${
                        Math.abs(ts.stop_loss_pct || 4) < 3 ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
                        : Math.abs(ts.stop_loss_pct || 4) < 6 ? 'text-amber-400 bg-amber-500/10 border-amber-500/30'
                        : 'text-red-400 bg-red-500/10 border-red-500/30'
                      }`}>
                        {Math.abs(ts.stop_loss_pct || 4) < 3 ? 'LOW' : Math.abs(ts.stop_loss_pct || 4) < 6 ? 'MEDIUM' : 'HIGH'}
                      </span>
                    </div>
                    {ts.hold_strategy && (
                      <div className="flex items-start gap-2">
                        <span className="text-muted text-[10px] w-20 mt-0.5">Hold</span>
                        <span className="text-slate-300 text-[11px] font-semibold">
                          {ts.hold_strategy?.includes('5') || ts.hold_strategy?.includes('swing') ? '5–15 trading days' : 'Position trade'}
                        </span>
                      </div>
                    )}

                    {/* Auto-trade status — makes it explicit whether the AI agent
                        will act on this signal, vs. it being a manual-only suggestion. */}
                    {(isBuy || isSell) && conf != null && (
                      conf >= AUTO_TRADE_FLOOR ? (
                        <div className="flex items-start gap-2 mt-1 rounded-lg bg-profit/5 border border-profit/20 px-2 py-1.5">
                          <Zap size={12} className="text-profit shrink-0 mt-0.5" />
                          <span className="text-[11px] text-profit leading-snug">
                            <b>Agent will auto-trade</b> — confidence {conf}% ≥ {AUTO_TRADE_FLOOR}% floor.
                          </span>
                        </div>
                      ) : (
                        <div className="flex items-start gap-2 mt-1 rounded-lg bg-amber-500/5 border border-amber-500/20 px-2 py-1.5">
                          <ShieldAlert size={12} className="text-amber-400 shrink-0 mt-0.5" />
                          <span className="text-[11px] text-amber-400/90 leading-snug">
                            <b>Signal only</b> — {conf}% is below the {AUTO_TRADE_FLOOR}% auto-trade floor.
                            The agent won't act; add to watchlist or trade manually.
                          </span>
                        </div>
                      )
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Trade plan col */}
            <div className="lg:col-span-5">
              <div className="grid grid-cols-2 gap-2.5">
                {verdictPending ? (
                  Array.from({ length: 4 }).map((_, i) => <Skel key={i} w="w-full" h="h-16" />)
                ) : ts.entry_low ? (
                  <>
                    <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                      <div className="text-muted text-[10px] uppercase tracking-wider">Entry zone</div>
                      <div className="font-mono text-slate-100 text-base font-bold mt-1">
                        ₹{fmt(ts.entry_low)} – {fmt(ts.entry_high)}
                      </div>
                      {ltp && ts.entry_low && ltp >= ts.entry_low && ltp <= (ts.entry_high ?? ts.entry_low * 1.1) && (
                        <div className="text-profit text-[10px] mt-0.5">✓ CMP inside zone</div>
                      )}
                    </div>
                    <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                      <div className="text-muted text-[10px] uppercase tracking-wider">Stop loss</div>
                      <div className="font-mono text-loss text-base font-bold mt-1">₹{fmt(ts.stop_loss)}</div>
                      {ts.stop_loss_pct && <div className="text-muted text-[10px] mt-0.5">{pct(ts.stop_loss_pct)} · 1× ATR</div>}
                    </div>
                    <div className="bg-white/[0.04] border border-border rounded-lg p-3 col-span-2">
                      <div className="text-muted text-[10px] uppercase tracking-wider mb-1">Targets</div>
                      <div className="flex items-center gap-3 font-mono">
                        {[['T1', ts.target_1, ts.target_1_pct], ['T2', ts.target_2, ts.target_2_pct]].map(([lbl, val, p]) =>
                          val ? (
                            <div key={lbl}>
                              <span className="text-muted text-[10px]">{lbl}</span>{' '}
                              <span className="text-profit text-base font-bold">₹{fmt(val)}</span>
                              {p && <span className="text-profit text-[10px] ml-0.5">({pct(p)})</span>}
                            </div>
                          ) : null
                        )}
                        {ts.risk_reward && (
                          <div className="ml-auto">
                            <span className="text-muted text-[10px]">R:R</span>{' '}
                            <span className="text-slate-200 text-base font-bold">1 : {ts.risk_reward}</span>
                          </div>
                        )}
                      </div>
                    </div>
                    {ts.when_to_buy && (
                      <div className="bg-profit/5 border border-profit/20 rounded-lg p-3">
                        <div className="text-muted text-[10px] uppercase tracking-wider">Best buy window</div>
                        <div className="text-slate-200 text-xs mt-1 leading-snug line-clamp-2">{ts.when_to_buy.replace(/\*\*/g, '').split('\n')[0].trim()}</div>
                      </div>
                    )}
                    {ts.when_to_sell && (
                      <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                        <div className="text-muted text-[10px] uppercase tracking-wider">Exit trigger</div>
                        <div className="text-slate-200 text-xs mt-1 leading-snug line-clamp-2">
                          {ts.when_to_sell.replace(/\*\*/g, '').split('\n')
                            .map(l => l.trim()).filter(Boolean)
                            .find(l => !l.endsWith(':') && l.length > 5)
                            ?.replace(/^[•·]\s*/, '') ?? '—'}
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="col-span-2 bg-white/[0.03] border border-border rounded-lg p-4 text-center">
                    <div className="text-muted text-sm">{deepSettled ? 'Setup data unavailable' : 'Trade setup loading…'}</div>
                    {!deepSettled && <div className="text-muted text-xs mt-1">deep analysis runs in background</div>}
                  </div>
                )}
              </div>
            </div>

            {/* Why now col */}
            <div className="lg:col-span-4">
              <div className="bg-white/[0.04] border border-border rounded-lg p-3 h-full">
                <div className="text-profit text-[10px] uppercase tracking-wider font-bold mb-2">Why now?</div>
                {verdictPending ? (
                  <div className="space-y-2">{Array.from({length:3}).map((_,i)=><Skel key={i} w="w-full" h="h-3"/>)}</div>
                ) : deep?.reasoning ? (
                  <>
                    {(deep.reasoning.bullish || []).slice(0,3).map((b,i)=>(
                      <div key={i} className="flex gap-2 text-xs text-slate-300 mb-1.5">
                        <span className="text-profit shrink-0">›</span><span>{b}</span>
                      </div>
                    ))}
                    {(deep.reasoning.bearish || []).slice(0,2).map((b,i)=>(
                      <div key={i} className="flex gap-2 text-xs text-slate-400 mb-1.5">
                        <span className="text-loss shrink-0">›</span><span>{b}</span>
                      </div>
                    ))}
                  </>
                ) : reasoning.sector_name ? (
                  <div className="space-y-1.5 text-xs text-slate-300">
                    <div className="flex gap-2"><span className="text-cyan shrink-0">›</span> Sector: {reasoning.sector_name} ({reasoning.sector_mood})</div>
                    <div className="flex gap-2"><span className="text-cyan shrink-0">›</span> Market regime: {reasoning.regime}</div>
                    <div className="flex gap-2"><span className="text-cyan shrink-0">›</span> News tone: {reasoning.news_tone}</div>
                  </div>
                ) : (
                  <div className="text-muted text-sm">Loading reasoning…</div>
                )}
              </div>
            </div>
          </div>

          {/* CTAs */}
          <div className="relative mt-4 flex gap-3">
            <button className="flex-1 bg-profit/10 hover:bg-profit/20 border border-profit/30 text-profit font-bold rounded-lg py-2.5 text-sm flex items-center justify-center gap-2 transition-colors">
              <TrendingUp size={14} /> Open trade ticket
            </button>
            <button className="flex-1 bg-white/[0.04] hover:bg-white/[0.07] border border-border text-slate-300 font-semibold rounded-lg py-2.5 text-sm flex items-center justify-center gap-2 transition-colors">
              <Bell size={14} /> Set price alert
            </button>
            <button
              onClick={toggleWatchlist}
              disabled={wlLoading}
              className={`flex-1 border font-semibold rounded-lg py-2.5 text-sm flex items-center justify-center gap-2 transition-colors ${
                inWatchlist
                  ? 'bg-amber-500/10 hover:bg-red-500/10 border-amber-500/30 text-amber-400 hover:text-red-400 hover:border-red-500/30'
                  : 'bg-white/[0.04] hover:bg-white/[0.07] border-border text-slate-300'
              }`}
            >
              <Star size={14} className={inWatchlist ? 'fill-amber-400' : ''} />
              {wlLoading ? '…' : inWatchlist ? 'In watchlist' : 'Add to watchlist'}
            </button>
          </div>
        </div>

        {/* ₹10k Scenario card */}
        {!verdictPending && (ts.entry_low || ltp) && (
          <InvestCard tradeSetup={ts} ltp={ltp} signal={signal} />
        )}

        {/* Confidence breakdown — hub-only (tracked symbols) */}
        {!loading && Object.keys(comp).length > 0 && (
          <div className="bg-card rounded-2xl border border-border p-5 mt-3">
            <div className="flex items-center gap-2 mb-3">
              <ShieldAlert size={14} className="text-cyan" />
              <span className="text-slate-200 text-sm font-semibold">Why confidence is {conf ?? '—'}%</span>
              <span className="ml-auto text-muted text-[10px]">7-component ensemble · click any chip for detail</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2.5 text-xs">
              {[
                ['Technicals',    comp.technical,   isBuy ? 'EMA stack · RSI · MACD' : 'Bearish structure'],
                ['News/Sentiment',comp.news,         reasoning.news_tone ? `${reasoning.news_tone} sentiment` : null],
                ['Fundamentals',  comp.fundamental,  fund ? `ROE ${fmt(fund.roe)}% · ROCE ${fmt(fund.roce)}%` : null],
                ['Sector',        comp.sector,       reasoning.sector_name ? `${reasoning.sector_name} ${reasoning.sector_mood}` : null],
                ['Macro',         comp.macro,        reasoning.regime ? `Regime: ${reasoning.regime}` : null],
                ['Earnings',      comp.earnings,     null],
                ['Options',       comp.options,      null],
              ].filter(([,v]) => v != null).map(([k, v, note]) => {
                const pos = v >= 0;
                const pct_width = Math.min(Math.abs(v) * 5, 100);
                return (
                  <div key={k}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-muted">{k}</span>
                      <span className={`font-mono font-semibold ${pos ? 'text-profit' : 'text-loss'}`}>
                        {pos ? '+' : ''}{Math.round(v)}
                      </span>
                    </div>
                    <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${pos ? 'bg-profit' : 'bg-loss'}`}
                        style={{ width: `${pct_width}%` }} />
                    </div>
                    {note && <div className="text-muted text-[10px] mt-0.5 truncate">{note}</div>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Scenario cards */}
        {!loading && (ts.target_1 || comp.technical) && (
          <div className="mt-3">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="text-slate-200 text-sm font-semibold">If price goes…</span>
              <span className="ml-auto text-muted text-[10px]">Think in probabilities</span>
            </div>
            <div className="grid grid-cols-3 gap-2.5">
              {[
                { label: 'Bullish', prob: isSell ? '20%' : '48%', cls: 'border-profit/25 bg-profit/5', textCls: 'text-profit',
                  title: ts.target_2 ? `Target path → ₹${fmt(ts.target_2)}` : 'Continued uptrend',
                  desc: isSell ? 'Reversal only if price closes above resistance with volume.' : 'If key resistances break with volume. Catalyst: sector momentum continues.' },
                { label: 'Sideways', prob: '32%', cls: 'border-amber-500/25 bg-amber-500/5', textCls: 'text-amber-400',
                  title: `Range ${ts.stop_loss ? `₹${fmt(ts.stop_loss)}` : '—'} – ${ts.target_1 ? `₹${fmt(ts.target_1)}` : '—'}`,
                  desc: 'Consolidation until next catalyst. Wait for a clean breakout or breakdown.' },
                { label: 'Bearish', prob: isSell ? '48%' : '20%', cls: 'border-red-500/25 bg-red-500/5', textCls: 'text-red-400',
                  title: ts.stop_loss ? `Risk zone → ₹${fmt(ts.stop_loss)}` : 'Downside risk',
                  desc: isSell ? 'Primary scenario — honour stop loss.' : 'If stop loss breaks, exit and re-evaluate.' },
              ].map(s => (
                <div key={s.label} className={`border ${s.cls} rounded-xl p-4`}>
                  <div className="flex items-center justify-between mb-2">
                    <span className={`text-[11px] font-bold uppercase tracking-wider ${s.textCls}`}>{s.label}</span>
                    <span className={`font-mono text-[10px] ${s.textCls}`}>{s.prob}</span>
                  </div>
                  <div className={`text-sm font-semibold ${s.textCls} leading-snug`}>{s.title}</div>
                  <div className="text-muted text-[11px] mt-1.5 leading-relaxed">{s.desc}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 2 — WHAT CHANGED TODAY (promoted)
      ═══════════════════════════════════════════════════════════════ */}
      <section className="px-5 pb-6 border-t border-border" style={{ background: '#0A1120' }}>
        <div className="pt-4">
          <SectionLabel color="text-cyan">
            Section 2 · What changed today
            <span className="ml-auto text-[10px] text-muted font-normal normal-case tracking-normal">
              {deepSettled ? `${news.length} events` : '…'}
            </span>
          </SectionLabel>

          {!deepSettled ? (
            <div className="space-y-2">{Array.from({length:3}).map((_,i)=><Skel key={i} w="w-full" h="h-16"/>)}</div>
          ) : news.length > 0 ? (
            <div className="bg-card rounded-xl border border-border overflow-hidden">
              {news.slice(0, 5).map((n, i) => <NewsRow key={i} n={n} divider={i > 0} />)}
              {news.length > 5 && (
                <button className="w-full px-4 py-2.5 border-t border-border text-muted text-xs hover:bg-white/[0.03] text-center">
                  Show {news.length - 5} more events
                </button>
              )}
            </div>
          ) : (
            <div className="bg-card border border-border rounded-xl px-4 py-6 text-center text-muted text-sm">
              No recent events for this symbol.
            </div>
          )}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 3 — INTELLIGENCE SNAPSHOT
      ═══════════════════════════════════════════════════════════════ */}
      <section className="px-5 pb-6" style={{ background: '#080D1A' }}>
        <SectionLabel>
          Section 3 · Intelligence snapshot
          {!verdictPending && !isTracked && (
            <span className="ml-auto text-[10px] text-amber-400/90 font-normal normal-case tracking-normal">
              Technical-only — symbol not in hub universe
            </span>
          )}
        </SectionLabel>
        {verdictPending ? (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            {Array.from({length:7}).map((_,i)=><Skel key={i} w="w-full" h="h-24"/>)}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            {/* Technical works for every symbol — from hub score, else derived from deep composite. */}
            <IntelChip label="Technical"    rawScore={isTracked ? comp.technical : (deep?.composite_score ?? 0)} icon={Activity} note={indicators.rsi ? `RSI ${fmt(indicators.rsi,1)} · ${indicators.ema_trend||''}` : null} />
            <IntelChip label="Fundamentals" rawScore={comp.fundamental}  icon={BookOpen}  noData={!isTracked && !fund} note={fund ? `ROE ${fmt(fund.roe)}% · ROCE ${fmt(fund.roce)}%` : null} />
            <IntelChip label="Sentiment"    rawScore={comp.news}         icon={Zap}       noData={!isTracked} note={reasoning.news_tone ? `${reasoning.news_tone}` : null} />
            <IntelChip label="Sector"       rawScore={comp.sector}       icon={PieChart}  noData={!isTracked} note={reasoning.sector_name ? `${reasoning.sector_name} · ${reasoning.sector_mood||''}` : null} />
            <IntelChip label="Macro"        rawScore={comp.macro}        icon={BarChart2} noData={!isTracked} note={reasoning.regime ? `Regime: ${reasoning.regime}` : null} />
            <IntelChip label="Earnings"     rawScore={comp.earnings}     icon={BookOpen}  noData={!isTracked} note={reasoning.fund_grade ? `Grade: ${reasoning.fund_grade}` : null} />
            <div className="bg-gradient-to-br from-card to-blue-900/20 border border-cyan/30 rounded-xl p-4 cursor-pointer">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-cyan uppercase tracking-wider font-semibold">Overall</span>
                <span className={`w-2 h-2 rounded-full shrink-0 ${isBuy ? 'bg-profit' : isSell ? 'bg-loss' : 'bg-amber-400'}`} />
              </div>
              <div className={`text-base font-bold leading-tight ${signalColor}`}>
                {isBuy ? 'Buy' : isSell ? 'Sell' : 'Hold'} · {conf != null ? `${conf}%` : '—'} conf
              </div>
              <DotMeter value={conf ?? 0} colorClass={signalColor} />
              <div className="text-muted text-[10px] mt-2">Conviction {convictionLabel} · score {score != null ? fmt(score,1) : '—'}</div>
            </div>
          </div>
        )}
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 4 — CHART
      ═══════════════════════════════════════════════════════════════ */}
      <section className="px-5 pb-6 border-t border-border" style={{ background: '#0A1120' }}>
        <div className="pt-4">
          <SectionLabel>Section 4 · Chart</SectionLabel>
          <div className="rounded-xl border border-border overflow-hidden" style={{ height: 420 }}>
            <CandlestickChart
              symbol={nsSymbol}
              name={fund?.company_name || display}
              fillParent
              defaultTimeframe="1h"
              showIndicators={true}
              showVolume={true}
              embedded={false}
            />
          </div>
          {/* Indicator strip */}
          {indicators.rsi && (
            <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted">
              <span className={indicators.rsi_signal === 'OVERBOUGHT' ? 'text-loss' : indicators.rsi_signal === 'OVERSOLD' ? 'text-profit' : 'text-muted'}>
                RSI {fmt(indicators.rsi, 1)} ({indicators.rsi_signal || 'neutral'})
              </span>
              {indicators.macd != null && <span>MACD {fmt(indicators.macd, 2)}</span>}
              {indicators.ema_trend && <span>EMA trend: <span className={indicators.ema_trend === 'BULLISH' ? 'text-profit' : 'text-loss'}>{indicators.ema_trend}</span></span>}
              {indicators.supertrend && <span>Supertrend: <span className={indicators.supertrend === 'BUY' ? 'text-profit' : 'text-loss'}>{indicators.supertrend}</span></span>}
              {indicators.bb_position && <span>BB: {indicators.bb_position}</span>}
            </div>
          )}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 5 — AI RESEARCH REPORT
      ═══════════════════════════════════════════════════════════════ */}
      <section className="px-5 pb-6" style={{ background: '#080D1A' }}>
        <SectionLabel>Section 5 · AI equity research</SectionLabel>

        {/* Executive summary */}
        <div className="rounded-2xl border border-blue-500/20 p-5 mb-4 relative overflow-hidden"
          style={{ background: 'linear-gradient(145deg,#131E30,#0F1829)', boxShadow: '0 0 40px -15px rgba(139,92,246,0.3)' }}>
          <div className="flex items-start justify-between mb-3">
            <div className="text-violet-400 text-[10px] uppercase tracking-[0.2em] font-bold">Executive Summary</div>
            {conf != null && <div className="text-right"><div className="text-[10px] text-muted">Conviction</div><div className="font-mono text-violet-400 text-xl font-bold">{isBuy ? 'High' : 'Low'} · {conf}%</div></div>}
          </div>
          {verdictPending ? <Skel w="w-full" h="h-16" /> : (
            <p className="text-slate-200 text-sm leading-relaxed">
              {deep?.ai_summary || (
                isBuy
                  ? `${fund?.company_name || display} is showing a ${String(signal||'').toLowerCase().replace('_',' ')} signal with ${conf}% confidence. Technical indicators are ${(comp.technical||0) > 0 ? 'bullish' : 'bearish'} and the sector outlook is ${reasoning.sector_mood?.toLowerCase() || 'neutral'}.`
                  : `${fund?.company_name || display} is under selling pressure. Technical structure is ${(comp.technical||0) > 0 ? 'recovering' : 'bearish'}. Capital preservation takes priority — wait for reversal signals.`
              )}
            </p>
          )}
        </div>

        {/* Bull / Bear / Risks */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div className="border border-profit/20 bg-profit/5 rounded-xl p-4">
            <div className="text-profit text-[11px] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-3">
              <TrendingUp size={12} /> Bull Case
            </div>
            <ul className="space-y-2 text-xs text-slate-300 leading-relaxed">
              {(deep?.reasoning?.bullish || []).slice(0,4).map((b,i) => (
                <li key={i} className="flex gap-2"><span className="text-profit shrink-0">{i+1}.</span>{b}</li>
              ))}
              {(!deep?.reasoning?.bullish?.length) && (
                <>
                  {(comp.technical||0) > 0 && <li className="flex gap-2"><span className="text-profit">1.</span>Technical momentum bullish</li>}
                  {(comp.fundamental||0) > 0 && <li className="flex gap-2"><span className="text-profit">2.</span>Fundamentals above average</li>}
                  {fund?.promoter_holding > 50 && <li className="flex gap-2"><span className="text-profit">3.</span>Strong promoter holding {fmt(fund.promoter_holding)}%</li>}
                </>
              )}
            </ul>
          </div>

          <div className="border border-red-500/20 bg-red-500/5 rounded-xl p-4">
            <div className="text-red-400 text-[11px] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-3">
              <TrendingDown size={12} /> Bear Case
            </div>
            <ul className="space-y-2 text-xs text-slate-300 leading-relaxed">
              {(deep?.reasoning?.bearish || []).slice(0,4).map((b,i) => (
                <li key={i} className="flex gap-2"><span className="text-loss shrink-0">{i+1}.</span>{b}</li>
              ))}
              {(!deep?.reasoning?.bearish?.length) && (
                <>
                  {(comp.technical||0) < 0 && <li className="flex gap-2"><span className="text-loss">1.</span>Technical structure bearish</li>}
                  {fund?.debt_to_equity > 1 && <li className="flex gap-2"><span className="text-loss">2.</span>High debt D/E {fmt(fund.debt_to_equity)}</li>}
                  <li className="flex gap-2"><span className="text-loss">3.</span>Sector sentiment: {reasoning.sector_mood || 'mixed'}</li>
                </>
              )}
            </ul>
          </div>

          <div className="border border-amber-500/20 bg-amber-500/5 rounded-xl p-4">
            <div className="text-amber-400 text-[11px] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-3">
              <ShieldAlert size={12} /> Key Risks
            </div>
            <ul className="space-y-2 text-xs text-slate-300 leading-relaxed">
              {ts.when_to_sell && <li className="flex gap-2"><span className="text-amber-400">!</span>{ts.when_to_sell.split('.')[0].replace(/\*\*/g,'').trim()}</li>}
              {fund?.pledged_pct > 5 && <li className="flex gap-2"><span className="text-amber-400">!</span>Promoter pledge {fmt(fund.pledged_pct)}% — watch closely</li>}
              {(comp.sector||0) < -10 && <li className="flex gap-2"><span className="text-amber-400">!</span>Sector rotation headwind</li>}
              <li className="flex gap-2"><span className="text-amber-400">!</span>Regime: {reasoning.regime || 'uncertain'} — adjust position size</li>
            </ul>
          </div>
        </div>

        {/* Entry / Exit strategy + Suitability */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-card border border-border rounded-xl p-4">
            <div className="text-slate-200 text-sm font-semibold mb-2 flex items-center gap-2">
              <TrendingUp size={13} className="text-profit" /> Entry strategy
            </div>
            <div className="text-xs text-slate-300 leading-relaxed" style={{whiteSpace:'pre-line'}}>
              {ts.when_to_buy ? ts.when_to_buy.replace(/\*\*/g, '') : 'No entry guidance available.'}
            </div>
          </div>
          <div className="bg-card border border-border rounded-xl p-4">
            <div className="text-slate-200 text-sm font-semibold mb-2 flex items-center gap-2">
              <TrendingDown size={13} className="text-loss" /> Exit strategy
            </div>
            <div className="text-xs text-slate-300 leading-relaxed" style={{whiteSpace:'pre-line'}}>
              {ts.hold_strategy ? ts.hold_strategy.replace(/\*\*/g, '') : 'No exit guidance available.'}
            </div>
          </div>
          <div className="bg-card border border-border rounded-xl p-4">
            <div className="text-slate-200 text-sm font-semibold mb-3">Suitability</div>
            <div className="space-y-2 text-xs">
              {[
                ['Long-term (5y+)', isBuy && (comp.fundamental||0) > 0 ? 88 : 40],
                ['Swing (5–15d)',   isBuy && (comp.technical||0) > 10 ? 78 : isBuy ? 55 : 20],
                ['Dividend',        fund?.dividend_yield > 1 ? 70 : 30],
                ['Intraday',        20],
              ].map(([lbl, val]) => (
                <div key={lbl}>
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-muted">{lbl}</span>
                    <span className={`text-[10px] font-bold ${val >= 70 ? 'text-profit' : val >= 50 ? 'text-amber-400' : 'text-muted'}`}>
                      {val >= 70 ? 'Good fit' : val >= 50 ? 'Moderate' : 'Not ideal'}
                    </span>
                  </div>
                  <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${val >= 70 ? 'bg-profit' : val >= 50 ? 'bg-amber-400' : 'bg-slate-600'}`}
                      style={{ width: `${val}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          PROGRESSIVE DISCLOSURE
      ═══════════════════════════════════════════════════════════════ */}
      <div className="px-5 py-5" style={{ background: '#080D1A' }}>
        <div className="flex items-center gap-3">
          <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
          <span className="text-muted text-xs px-3 py-1.5 rounded-full bg-card border border-border">
            Decision answered above · expand sections below for deeper analysis
          </span>
          <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════
          DEEP TABS (SECTION 6+)
      ═══════════════════════════════════════════════════════════════ */}
      <section className="px-5 pb-8 space-y-2" style={{ background: '#080D1A' }}>
        {/* Company */}
        <DeepTab label="Company" subtitle="History · management · business model" icon={BookOpen}>
          {fund ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              {fund.company_name && <div><span className="text-muted text-xs">Name</span><div className="text-slate-200">{fund.company_name}</div></div>}
              {fund.sector        && <div><span className="text-muted text-xs">Sector</span><div className="text-slate-200">{fund.sector}</div></div>}
              {fund.market_cap_cr && <div><span className="text-muted text-xs">Mkt cap</span><div className="text-slate-200">₹{fmt(fund.market_cap_cr, 0)} Cr</div></div>}
              {fund.dividend_yield!= null && <div><span className="text-muted text-xs">Div yield</span><div className="text-slate-200">{fmt(fund.dividend_yield)}%</div></div>}
            </div>
          ) : <div className="text-muted text-sm">{fundLoading ? 'Loading company data…' : 'Company details not available for this symbol.'}</div>}
        </DeepTab>

        {/* Financials */}
        <DeepTab label="Financials" subtitle="PE · PB · ROE · ROCE · debt ratios" icon={BarChart2}>
          {fund ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                ['PE ratio',   fund.pe_ratio   != null ? fmt(fund.pe_ratio) : null],
                ['PB ratio',   fund.pb_ratio   != null ? fmt(fund.pb_ratio) : null],
                ['ROE',        fund.roe        != null ? fmt(fund.roe) + '%' : null],
                ['ROCE',       fund.roce       != null ? fmt(fund.roce) + '%' : null],
                ['Debt/Equity',fund.debt_to_equity != null ? fmt(fund.debt_to_equity, 2) : null],
                ['Rev growth 3y', fund.revenue_growth_3yr != null ? pct(fund.revenue_growth_3yr) : null],
                ['Profit growth 3y', fund.profit_growth_3yr != null ? pct(fund.profit_growth_3yr) : null],
                ['Div yield',  fund.dividend_yield != null ? fmt(fund.dividend_yield) + '%' : null],
              ].filter(([,v]) => v != null).map(([k, v]) => (
                <div key={k} className="bg-surface rounded-lg border border-border p-3">
                  <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                  <div className="font-mono text-slate-100 text-base font-bold mt-1">{v}</div>
                </div>
              ))}
            </div>
          ) : <div className="text-muted text-sm">{fundLoading ? 'Loading financials…' : 'Financials not available for this symbol.'}</div>}
        </DeepTab>

        {/* Ownership */}
        <DeepTab label="Ownership & Smart money" subtitle="Promoter · FII · DII · pledge" icon={Users}>
          {fund ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                ['Promoter', fund.promoter_holding != null ? fmt(fund.promoter_holding) + '%' : null, fund.promoter_holding > 50 ? 'text-profit' : 'text-muted'],
                ['FII',      fund.fii_holding      != null ? fmt(fund.fii_holding) + '%' : null, 'text-slate-200'],
                ['Pledged',  fund.pledged_pct      != null ? fmt(fund.pledged_pct) + '%' : null, fund.pledged_pct > 10 ? 'text-loss' : 'text-profit'],
                ['Fundamental score', fund.fundamental_score != null ? fmt(fund.fundamental_score, 0) + '/100' : null, 'text-cyan'],
              ].filter(([,v]) => v != null).map(([k, v, c]) => (
                <div key={k} className="bg-surface rounded-lg border border-border p-3">
                  <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                  <div className={`font-mono text-base font-bold mt-1 ${c}`}>{v}</div>
                </div>
              ))}
            </div>
          ) : <div className="text-muted text-sm">{fundLoading ? 'Loading ownership data…' : 'Ownership data not available for this symbol.'}</div>}
        </DeepTab>

        {/* Peers */}
        <DeepTab label="Peers" subtitle="Side-by-side comparison with sector peers" icon={Users} badge="Phase 2">
          <div className="text-muted text-sm">Peer comparison — Phase 2. In the meantime, use the <Link to="/" className="text-cyan hover:underline">Compare flow</Link> from the global search.</div>
        </DeepTab>

        {/* Technicals advanced */}
        <DeepTab label="Technicals (Advanced)" subtitle="Full indicator dashboard · pivots · Fibonacci" icon={Activity}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
            {Object.entries(indicators).filter(([k]) => typeof indicators[k] === 'number').slice(0, 8).map(([k, v]) => (
              <div key={k} className="bg-surface rounded-lg border border-border p-3">
                <div className="text-muted text-[10px] uppercase tracking-wider">{k.replace(/_/g,' ')}</div>
                <div className="font-mono text-slate-200 text-sm font-semibold mt-1">{fmt(v)}</div>
              </div>
            ))}
          </div>
          <p className="text-muted text-xs">
            Open the <Link to={`/chart?symbol=${nsSymbol}&name=${display}`} className="text-cyan hover:underline">full chart page</Link> for drawing tools, Fibonacci levels, and pattern detection.
          </p>
        </DeepTab>

        {/* Options */}
        <DeepTab label="Options" subtitle="Chain · OI · IV · max pain · PCR" icon={PieChart}>
          <div className="text-muted text-sm">Options chain available at <code className="text-cyan text-xs">/api/v1/india/options-chain/{display}</code>. Full UI — Phase 2.</div>
        </DeepTab>

        {/* Compare */}
        <DeepTab label="Compare" subtitle="RELIANCE vs ONGC vs BHARTIARTL" icon={BarChart2} badge="New" badgeColor="text-cyan">
          <div className="text-muted text-sm">Use ⌘K search → type a symbol → Quick action "Compare with peers".</div>
        </DeepTab>
      </section>
      </>}
    </div>
  );
}
