/**
 * StockDetail — /s/:symbol
 * Decision-first unified stock page.
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
import CandlestickChart, { AIPredictPanel } from '../components/chart/CandlestickChart';
import { useLivePrice } from '../contexts/LivePricesContext';

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderExpertMarkdown(text) {
  if (!text) return null
  const lines = text.split('\n')
  const result = []
  let key = 0

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    const line = raw.trim()

    if (!line) {
      result.push(<div key={key++} className="h-3" />)
      continue
    }

    // Heading: line that is entirely **...** or starts with #
    const headingMatch = line.match(/^#{1,3}\s+(.+)$/)
    const boldLineMatch = line.match(/^\*\*(.+)\*\*[:\s]*$/)
    if (headingMatch || boldLineMatch) {
      const txt = headingMatch ? headingMatch[1] : boldLineMatch[1]
      const isH1 = line.startsWith('# ') || (boldLineMatch && !line.match(/^\*\*[^*]+\*\*:/))
      result.push(
        <div key={key++} className={`font-bold mt-4 mb-1 ${isH1 ? 'text-violet-300 text-base' : 'text-cyan-300 text-sm'}`}>
          {txt}
        </div>
      )
      continue
    }

    // Numbered list: "1. " or "1) "
    const numMatch = line.match(/^(\d+)[.)]\s+(.*)$/)
    if (numMatch) {
      result.push(
        <div key={key++} className="flex items-start gap-2 my-1 ml-2">
          <span className="text-violet-400 font-semibold text-sm shrink-0 mt-0.5">{numMatch[1]}.</span>
          <span className="text-slate-200 text-sm leading-relaxed">{inlineFormat(numMatch[2])}</span>
        </div>
      )
      continue
    }

    // Bullet
    const bulletMatch = line.match(/^[-•*]\s+(.*)$/)
    if (bulletMatch) {
      result.push(
        <div key={key++} className="flex items-start gap-2 my-1 ml-2">
          <span className="text-violet-400 mt-1.5 shrink-0" style={{ fontSize: 6 }}>●</span>
          <span className="text-slate-200 text-sm leading-relaxed">{inlineFormat(bulletMatch[1])}</span>
        </div>
      )
      continue
    }

    // Normal paragraph
    result.push(
      <p key={key++} className="text-slate-300 text-sm leading-relaxed my-1">{inlineFormat(line)}</p>
    )
  }
  return result
}

function inlineFormat(text) {
  const parts = []
  const re = /(\*\*(.+?)\*\*|\*(.+?)\*|₹[\d,]+(?:\.\d+)?(?:\s*(?:Cr|L|K))?|[+-]?\d+\.?\d*%|\b(BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL|BULLISH|BEARISH)\b)/g
  let last = 0, m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) {
      parts.push(<strong key={m.index} className="font-semibold text-slate-100">{m[2]}</strong>)
    } else if (tok.startsWith('*')) {
      parts.push(<em key={m.index} className="italic text-slate-300">{m[3]}</em>)
    } else if (/^(BUY|STRONG_BUY|BULLISH)$/.test(tok)) {
      parts.push(<span key={m.index} className="mx-0.5 px-1.5 py-0.5 rounded text-xs font-bold bg-emerald-500/20 text-emerald-400">{tok}</span>)
    } else if (/^(SELL|STRONG_SELL|BEARISH)$/.test(tok)) {
      parts.push(<span key={m.index} className="mx-0.5 px-1.5 py-0.5 rounded text-xs font-bold bg-red-500/20 text-red-400">{tok}</span>)
    } else if (tok === 'HOLD') {
      parts.push(<span key={m.index} className="mx-0.5 px-1.5 py-0.5 rounded text-xs font-semibold bg-slate-700 text-slate-300">{tok}</span>)
    } else if (tok.startsWith('₹')) {
      parts.push(<span key={m.index} className="font-semibold text-sky-400">{tok}</span>)
    } else if (tok.includes('%')) {
      const isPos = tok.startsWith('+') || (!tok.startsWith('-') && parseFloat(tok) > 0)
      parts.push(<span key={m.index} className={isPos ? 'text-emerald-400 font-medium' : 'text-red-400 font-medium'}>{tok}</span>)
    } else {
      parts.push(tok)
    }
    last = m.index + tok.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : parts
}

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
    <div className="glass-panel rounded-xl p-4 hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.4)] transition-all duration-300">
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

function IntelChip({ label, rawScore, note, icon: Icon, noData, excluded, explanation }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = !!explanation;

  if (noData || excluded) {
    return (
      <div
        className={`glass-panel rounded-xl p-4 transition-all ${hasDetail ? 'cursor-pointer hover:-translate-y-1 hover:shadow-lg hover:border-white/20' : 'opacity-50'}`}
        onClick={() => hasDetail && setExpanded(e => !e)}
      >
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            {Icon && <Icon size={12} className="text-muted" />}
            <span className="text-[10px] text-muted uppercase tracking-wider">{label}</span>
          </div>
          {hasDetail
            ? <ChevronDown size={11} className={`text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
            : <span className="w-2 h-2 rounded-full shrink-0 bg-slate-600" />
          }
        </div>
        <div className="text-sm font-bold leading-tight text-slate-400">
          {explanation?.verdict || 'No data'}
        </div>
        {explanation && (
          <div className="text-[10px] text-muted mt-1 leading-snug">
            {explanation.weight_pct}% weight · {explanation.contribution > 0 ? '+' : ''}{explanation.contribution} pts
          </div>
        )}
        {!hasDetail && (
          <div className="text-[10px] text-muted mt-2 leading-snug">
            {excluded ? 'Excluded from score' : 'Not in tracked universe'}
          </div>
        )}
        {expanded && explanation && (
          <div className="mt-3 pt-3 border-t border-border/50 space-y-2">
            <div className="text-[11px] text-slate-300 leading-relaxed">{explanation.detail}</div>
            {(explanation.headlines || []).slice(0, 2).map((h, i) => (
              <div key={i} className="flex gap-1.5 text-[10px] text-muted">
                <span className="text-cyan shrink-0 mt-0.5">›</span><span>{h}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  const norm    = Math.max(0, Math.min(100, 50 + (rawScore ?? 0)));
  const isPos   = (rawScore ?? 0) >= 10;
  const isNeg   = (rawScore ?? 0) <= -10;
  const color   = isPos ? 'text-emerald-400' : isNeg ? 'text-red-400' : 'text-amber-400';
  const border  = isPos ? 'hover:border-emerald-500/40' : isNeg ? 'hover:border-red-500/40' : 'hover:border-amber-500/40';
  const verdict = isPos ? 'Bullish' : isNeg ? 'Bearish' : 'Neutral';

  return (
    <div
      className={`glass-panel rounded-xl p-4 transition-all duration-300 ${hasDetail ? 'cursor-pointer hover:-translate-y-1 hover:shadow-lg' : ''} ${border}`}
      onClick={() => hasDetail && setExpanded(e => !e)}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          {Icon && <Icon size={12} className="text-muted" />}
          <span className="text-[10px] text-muted uppercase tracking-wider">{label}</span>
        </div>
        {hasDetail
          ? <ChevronDown size={11} className={`text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
          : <span className={`w-2 h-2 rounded-full shrink-0 ${isPos ? 'bg-emerald-400' : isNeg ? 'bg-red-400' : 'bg-amber-400'}`} />
        }
      </div>
      <div className={`text-base font-bold leading-tight ${color}`}>{verdict}</div>
      <DotMeter value={norm} colorClass={color} />
      {note && !expanded && <div className="text-[10px] text-muted mt-2 leading-snug line-clamp-2">{note}</div>}
      {expanded && explanation && (
        <div className="mt-3 pt-3 border-t border-border/50 space-y-2">
          <div className="flex items-center gap-3 text-[10px] flex-wrap">
            <span className="text-muted">Score:</span>
            <span className={`font-mono font-bold ${color}`}>{explanation.score > 0 ? '+' : ''}{explanation.score}</span>
            <span className="text-muted">Weight:</span>
            <span className="text-slate-300">{explanation.weight_pct}%</span>
            <span className="text-muted">Impact:</span>
            <span className={`font-mono font-bold ${color}`}>{explanation.contribution > 0 ? '+' : ''}{explanation.contribution} pts</span>
          </div>
          <div className="text-[11px] text-slate-300 leading-relaxed">{explanation.detail}</div>
          {(explanation.headlines || []).slice(0, 2).map((h, i) => (
            <div key={i} className="flex gap-1.5 text-[10px] text-muted">
              <span className="text-cyan shrink-0 mt-0.5">›</span><span>{h}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Deep tab accordion ────────────────────────────────────────────────────────

function DeepTab({ label, subtitle, badge, badgeColor = '', icon: Icon, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="glass-panel rounded-xl overflow-hidden hover:shadow-md transition-shadow">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/5 transition-colors"
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

  const wsLive   = useLivePrice(nsSymbol);   // real-time from shared WebSocket

  const [price,         setPrice]         = useState(null);
  const [deep,          setDeep]          = useState(null);
  const [intel,         setIntel]         = useState(null);
  const [fund,          setFund]          = useState(null);
  const [companyProfile,setCompanyProfile]= useState(null);
  const [financials,    setFinancials]    = useState(null);
  const [peers,         setPeers]         = useState(null);
  const [screenerData,  setScreenerData]  = useState(null);  // Screener.in + NSE deep
  const [screenerLoading,setScreenerLoading] = useState(false);
  const [optionsResearch, setOptionsResearch] = useState(null);
  const [optionsResearchLoading, setOptionsResearchLoading] = useState(false);
  const [fnoStatus,     setFnoStatus]     = useState(null);  // authoritative NFO-master F&O eligibility
  const [optionsChain,  setOptionsChain]  = useState(null);  // live Kite-sourced per-stock chain
  const [fundLoading,   setFundLoading]   = useState(true);
  const [deepSettled,   setDeepSettled]   = useState(false);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState(null);
  const [autoFloor,     setAutoFloor]     = useState(30);
  const [inWatchlist,   setInWatchlist]   = useState(false);
  const [wlLoading,     setWlLoading]     = useState(false);
  const [upstoxData,    setUpstoxData]    = useState(null);
  const [upstoxStatus,  setUpstoxStatus]  = useState(null);
  const [upstoxLoading, setUpstoxLoading] = useState(false);

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

      // Fundamentals — may fetch on-demand (yfinance + Screener, ~5s first hit)
      apiFetch(`/api/v1/india/fundamentals/${encodeURIComponent(nsSymbol)}`)
        .then(d => setFund(d))
        .catch(() => {})
        .finally(() => setFundLoading(false));

      // Rich company profile, financials, peers — all non-blocking deep-tab data
      apiFetch(`/api/v1/india/company-profile/${encodeURIComponent(display)}`)
        .then(d => setCompanyProfile(d)).catch(() => {});
      apiFetch(`/api/v1/india/financials/${encodeURIComponent(display)}`)
        .then(d => setFinancials(d)).catch(() => {});
      apiFetch(`/api/v1/india/peers/${encodeURIComponent(display)}`)
        .then(d => setPeers(d)).catch(() => {});

      // Screener.in + NSE deep data — slowest (~5-8 s), load last
      setScreenerLoading(true);
      apiFetch(`/api/v1/india/screener-deep/${encodeURIComponent(display)}`)
        .then(d => setScreenerData(d))
        .catch(() => {})
        .finally(() => setScreenerLoading(false));

      // Agent-driven options research — Tavily discovers best sources dynamically
      setOptionsResearchLoading(true);
      apiFetch(`/api/v1/india/options-research/${encodeURIComponent(display)}`)
        .then(d => setOptionsResearch(d))
        .catch(() => {})
        .finally(() => setOptionsResearchLoading(false));

      // Authoritative F&O eligibility from the NFO instrument master (not mkt-cap)
      apiFetch(`/api/v1/india/fno-status/${encodeURIComponent(display)}`)
        .then(d => setFnoStatus(d)).catch(() => {});

      // Live per-stock option chain via Kite — reliable PCR/max-pain/IV (and it
      // persists, feeding the hub's per-stock options score for this symbol).
      apiFetch(`/api/v1/india/options-chain/${encodeURIComponent(display)}`)
        .then(d => setOptionsChain(d)).catch(() => {});

      // Check if symbol is in user watchlist (non-blocking)
      apiFetch('/api/v1/india/user-watchlist')
        .then(d => setInWatchlist((d.symbols || []).includes(nsSymbol)))
        .catch(() => {});

      // Live agent auto-trade threshold (so the "signal only" floor stays in
      // sync with the backend PAPER_CONFIDENCE_THRESHOLD instead of hardcoding).
      apiFetch('/api/v1/india/market-scanner/shortlist?limit=1')
        .then(d => { if (d?.auto_trade_threshold != null) setAutoFloor(d.auto_trade_threshold); })
        .catch(() => {});

      // Upstox — check auth first, then fetch overview only when authenticated.
      // Both calls are non-blocking and silently fail when Upstox is not set up.
      setUpstoxLoading(true);
      apiFetch('/api/v1/upstox/status')
        .then(status => {
          setUpstoxStatus(status);
          if (status?.authenticated) {
            return apiFetch(`/api/v1/upstox/overview/${encodeURIComponent(display)}`);
          }
          return null;
        })
        .then(d => { if (d) setUpstoxData(d); })
        .catch(() => {})
        .finally(() => setUpstoxLoading(false));
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
    // Poll price every 15 s — matches backend refresh cycle
    pollRef.current = setInterval(() => {
      apiFetch(`/api/v1/india/live-prices/${encodeURIComponent(nsSymbol)}`)
        .then(d => setPrice(d))
        .catch(() => {});
    }, 15_000);
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
  const aw         = reasoning.active_weights ?? {};
  const ts         = deep?.trade_setup ?? {};
  const indicators = deep?.indicators ?? {};
  // Priority: WS tick (real-time) → REST poll (30 s) → deep analysis (one-shot)
  const ltp        = wsLive?.price ?? price?.price ?? deep?.ltp ?? null;
  const ltpChange  = wsLive?.change_pct ?? price?.change_pct ?? deep?.change_pct ?? null;

  // Build "What changed today" events from real data + synthetic indicator events.
  // This ensures the section is never blank even when there's no news for this stock.
  const todayEvents = (() => {
    const evts = [];
    const now = new Date().toISOString();
    // 1. Price movement (always present once deep loads)
    if (deep?.change_pct != null) {
      const up = deep.change_pct >= 0;
      evts.push({
        headline: `Price ${up ? 'up' : 'down'} ${Math.abs(deep.change_pct).toFixed(2)}% today — LTP ₹${deep.ltp?.toFixed(2) ?? '—'}`,
        source: 'Market Data',
        published_at: now,
        sentiment: up ? 'positive' : 'negative',
        score: deep.change_pct,
        impact: up ? 'Bullish momentum — price above yesterday close' : 'Selling pressure — price below yesterday close',
      });
    }
    // 2. AI signal
    if (signal && score != null) {
      const isBuyEv = String(signal).includes('BUY');
      const isSellEv = String(signal).includes('SELL');
      evts.push({
        headline: `AI Signal: ${signal.replace('_', ' ')} · ${Math.min(100, Math.round(Math.abs(score)))}% confidence`,
        source: 'Prajna AI',
        published_at: deep?.as_of ?? now,
        sentiment: isBuyEv ? 'positive' : isSellEv ? 'negative' : 'neutral',
        score: isBuyEv ? score : -Math.abs(score),
        impact: `Hub score ${score?.toFixed(1)} · Regime: ${reasoning.regime || 'unknown'}`,
      });
    }
    // 3. RSI state (only if notable)
    const rsi = indicators.rsi;
    if (rsi != null) {
      if (indicators.rsi_signal === 'OVERBOUGHT')
        evts.push({ headline: `RSI ${rsi.toFixed(1)} — Overbought (>70): momentum extreme, watch for reversal`, source: 'Technical', published_at: now, sentiment: 'negative', score: -5 });
      else if (indicators.rsi_signal === 'OVERSOLD')
        evts.push({ headline: `RSI ${rsi.toFixed(1)} — Oversold (<30): potential bounce setup`, source: 'Technical', published_at: now, sentiment: 'positive', score: 5 });
    }
    // 4. MACD cross
    if (indicators.macd_cross) {
      const bull = indicators.macd_cross === 'BULLISH_CROSS';
      evts.push({ headline: `MACD ${bull ? 'bullish' : 'bearish'} crossover detected`, source: 'Technical', published_at: now, sentiment: bull ? 'positive' : 'negative', score: bull ? 8 : -8 });
    }
    // 5. Supertrend direction change
    if (indicators.supertrend_dir) {
      const bull = indicators.supertrend_dir === 'UP';
      evts.push({ headline: `Supertrend direction: ${bull ? '▲ Bullish' : '▼ Bearish'} — price ${bull ? 'above' : 'below'} Supertrend line`, source: 'Technical', published_at: now, sentiment: bull ? 'positive' : 'negative', score: bull ? 6 : -6 });
    }
    // 6. Real news articles (from deep analysis Finnhub/RSS)
    for (const n of (deep?.news ?? [])) {
      evts.push(n);
    }
    return evts;
  })();

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
    <div className="-m-4 md:-m-6 flex flex-col min-h-screen" style={{ background: '#080D1A' }}>

      {/* ── Sticky symbol header ─────────────────────────────────────── */}
      <div className="sticky -top-4 md:-top-6 z-30 border-b border-white/5 px-4 md:px-5 py-3 flex flex-col md:flex-row md:items-center justify-between gap-3 bg-black/40 backdrop-blur-md">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <button onClick={() => navigate(-1)} className="text-muted hover:text-slate-300 p-1 rounded-lg hover:bg-white/5 shrink-0">
            <ArrowLeft size={16} />
          </button>
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-900 to-blue-600 grid place-items-center font-bold text-white text-sm shrink-0">
            {display?.[0] ?? '?'}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-slate-100 font-semibold text-base truncate">{fund?.company_name || price?.name || display}</span>
              <span className="font-mono text-[10px] bg-white/5 border border-border px-1.5 py-0.5 rounded text-muted shrink-0">{display}</span>
              <span className="text-[10px] bg-white/5 border border-border px-1.5 py-0.5 rounded text-muted shrink-0">NSE · EQ</span>
              {isTracked ? (
                <span className="text-[10px] font-bold text-violet-300 bg-violet-500/10 border border-violet-500/30 px-1.5 py-0.5 rounded shrink-0"
                  title="Deep-scored by the Hub: technical + news + fundamentals + earnings + sector + macro + options">
                  HUB 7-FACTOR
                </span>
              ) : !verdictPending && (
                <span className="text-[10px] font-semibold text-amber-400/90 bg-amber-500/10 border border-amber-500/25 px-1.5 py-0.5 rounded shrink-0"
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
        </div>
        <div className="flex items-center justify-between md:justify-end gap-4 shrink-0">
          <div className="flex items-baseline gap-2 shrink-0">
            {loading && !price ? <Skel w="w-28" h="h-8" /> : (
              <>
                <span className="font-mono text-2xl font-bold text-slate-100">{ltp ? `₹${fmt(ltp)}` : price?.price ? `₹${fmt(price.price)}` : '—'}</span>
                {ltpChange != null && (
                  <span className={`font-mono text-sm font-semibold ${ltpChange >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {pct(ltpChange)}
                  </span>
                )}
              </>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button onClick={toggleWatchlist} disabled={wlLoading} className={`p-1.5 rounded-lg hover:bg-white/5 transition-colors ${inWatchlist ? 'text-amber-400' : 'text-muted hover:text-amber-400'}`} title={inWatchlist ? 'Remove from watchlist' : 'Add to watchlist'}><Star size={15} className={inWatchlist ? 'fill-amber-400' : ''} /></button>
            <button className="text-muted hover:text-cyan p-1.5 rounded-lg hover:bg-white/5 transition-colors" title="Alert"><Bell size={15} /></button>
            <button className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5 transition-colors" title="Share"><Share2 size={15} /></button>
            <button onClick={load} className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5" title="Refresh">
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>
      </div>

      {/* Price strip */}
      {(price || deep) && (
        <div className="px-5 py-2.5 border-b border-border flex items-center gap-6 text-xs flex-wrap glass-panel">
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

      {/* Full-page spinner — visible during the very first load before price data arrives */}
      {loading && !price && !deep && (
        <div className="flex-1 grid place-items-center py-24">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-cyan/30 border-t-cyan rounded-full animate-spin" />
            <span className="text-muted text-sm">Loading {display}…</span>
          </div>
        </div>
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
      <section className="px-5 pt-6 pb-6 glass-panel">
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
        <div className="rounded-2xl border border-white/10 p-5 mb-3 relative overflow-hidden glass-panel"
          style={{ boxShadow: `0 0 40px -15px ${signalGlow}` }}>
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
                    {deepSettled
                      ? <div className="text-muted text-xs mt-1">Insufficient price history — SME/illiquid stock or upper-circuit lock streak</div>
                      : <div className="text-muted text-xs mt-1">deep analysis runs in background</div>
                    }
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
      <section className="px-5 pb-6 border-t border-border glass-panel">
        <div className="pt-4">
          <SectionLabel color="text-cyan">
            Today's activity
            <span className="ml-auto text-[10px] text-muted font-normal normal-case tracking-normal">
              {deepSettled ? `${todayEvents.length} events` : '…'}
            </span>
          </SectionLabel>

          {!deepSettled ? (
            <div className="space-y-2">{Array.from({length:3}).map((_,i)=><Skel key={i} w="w-full" h="h-16"/>)}</div>
          ) : (
            <div className="bg-card rounded-xl border border-border overflow-hidden">
              {todayEvents.slice(0, 6).map((n, i) => <NewsRow key={i} n={n} divider={i > 0} />)}
              {todayEvents.length === 0 && (
                <div className="px-4 py-6 text-center text-muted text-sm">Loading activity…</div>
              )}
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
          {!verdictPending && isTracked && (
            <span className="ml-auto text-[10px] text-muted font-normal normal-case tracking-normal">
              Click any factor for detailed explanation
            </span>
          )}
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
            <IntelChip label="Technical"
              rawScore={isTracked ? comp.technical : (deep?.composite_score ?? 0)}
              icon={Activity}
              note={indicators.rsi ? `RSI ${fmt(indicators.rsi,1)} · ${indicators.ema_trend||''}` : null}
              explanation={intel?.factor_explanations?.technical}
            />
            <IntelChip label="Fundamentals"
              rawScore={comp.fundamental}
              icon={BookOpen}
              noData={!isTracked && !fund}
              excluded={isTracked && aw.fundamental === 0}
              note={fund ? `ROE ${fmt(fund.roe)}% · ROCE ${fmt(fund.roce)}%` : null}
              explanation={intel?.factor_explanations?.fundamental}
            />
            <IntelChip label="Sentiment"
              rawScore={comp.news}
              icon={Zap}
              noData={!isTracked}
              excluded={isTracked && aw.news === 0}
              note={reasoning.news_tone ? `${reasoning.news_tone}` : null}
              explanation={intel?.factor_explanations?.news}
            />
            <IntelChip label="Sector"
              rawScore={comp.sector}
              icon={PieChart}
              noData={!isTracked}
              excluded={isTracked && aw.sector === 0}
              note={reasoning.sector_name ? `${reasoning.sector_name} · ${reasoning.sector_mood||''}` : null}
              explanation={intel?.factor_explanations?.sector}
            />
            <IntelChip label="Macro"
              rawScore={comp.macro}
              icon={BarChart2}
              noData={!isTracked}
              note={reasoning.regime ? `Regime: ${reasoning.regime}` : null}
              explanation={intel?.factor_explanations?.macro}
            />
            <IntelChip label="Earnings"
              rawScore={comp.earnings}
              icon={BookOpen}
              noData={!isTracked}
              excluded={isTracked && aw.earnings === 0}
              note={reasoning.fund_grade ? `Grade: ${reasoning.fund_grade}` : null}
              explanation={intel?.factor_explanations?.earnings}
            />
            <div className="bg-gradient-to-br from-card to-blue-900/20 border border-cyan/30 rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-cyan uppercase tracking-wider font-semibold">Overall</span>
                <span className={`w-2 h-2 rounded-full shrink-0 ${isBuy ? 'bg-profit' : isSell ? 'bg-loss' : 'bg-amber-400'}`} />
              </div>
              <div className={`text-base font-bold leading-tight ${signalColor}`}>
                {isBuy ? 'Buy' : isSell ? 'Sell' : 'Hold'} · {conf != null ? `${conf}%` : '—'} conf
              </div>
              <DotMeter value={conf ?? 0} colorClass={signalColor} />
              <div className="text-muted text-[10px] mt-2">Conviction {convictionLabel} · score {score != null ? fmt(score,1) : '—'}</div>
              {isTracked && score != null && (
                <div className="text-muted text-[10px] mt-1">
                  7-factor weighted model
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 4 — CHART
      ═══════════════════════════════════════════════════════════════ */}
      <section className="relative px-4 md:px-6 py-8 border-t border-white/5 bg-[#080D1A]/50">
        <div className="absolute inset-0 bg-gradient-to-b from-accent/5 to-transparent pointer-events-none opacity-40" />
        <div className="relative z-10 max-w-7xl mx-auto">
          
          {/* Header */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-accent/20 to-accent/5 flex items-center justify-center border border-accent/20 shadow-lg shadow-accent/10">
                <BarChart2 size={20} className="text-accent" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-slate-100 tracking-tight">Interactive Price Action</h2>
                <p className="text-xs text-muted mt-0.5 font-medium">Advanced multi-timeframe analysis & technical indicators</p>
              </div>
            </div>
          </div>

          {/* Chart Container */}
          <div className="rounded-2xl border border-white/10 overflow-hidden bg-[#0A1121] shadow-[0_8px_32px_rgba(0,0,0,0.4)]" style={{ height: 480 }}>
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

          <AIPredictPanel symbol={nsSymbol} />

          {/* Indicator Strip (Pill Badges) */}
          {indicators.rsi && (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <div className={`px-3 py-1.5 rounded-lg border flex items-center gap-2 ${indicators.rsi_signal === 'OVERBOUGHT' ? 'bg-red-500/10 border-red-500/20 text-red-400' : indicators.rsi_signal === 'OVERSOLD' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-white/5 border-white/10 text-slate-300'}`}>
                <span className="text-[10px] uppercase tracking-wider opacity-70 font-semibold">RSI</span>
                <span className="text-xs font-bold">{fmt(indicators.rsi, 1)}</span>
              </div>
              
              {indicators.macd != null && (
                <div className="px-3 py-1.5 rounded-lg border bg-white/5 border-white/10 text-slate-300 flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wider opacity-70 font-semibold">MACD</span>
                  <span className="text-xs font-bold">{fmt(indicators.macd, 2)}</span>
                </div>
              )}
              
              {indicators.ema_trend && (
                <div className={`px-3 py-1.5 rounded-lg border flex items-center gap-2 ${indicators.ema_trend === 'BULLISH' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border-red-500/20 text-red-400'}`}>
                   <span className="text-[10px] uppercase tracking-wider opacity-70 font-semibold">EMA</span>
                   <span className="text-xs font-bold">{indicators.ema_trend}</span>
                </div>
              )}

              {indicators.supertrend_dir && (
                <div className={`px-3 py-1.5 rounded-lg border flex items-center gap-2 ${indicators.supertrend_dir === 'BULLISH' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border-red-500/20 text-red-400'}`}>
                   <span className="text-[10px] uppercase tracking-wider opacity-70 font-semibold">Supertrend</span>
                   <span className="text-xs font-bold">{indicators.supertrend_dir}</span>
                </div>
              )}

              {indicators.bb_position && (
                <div className="px-3 py-1.5 rounded-lg border bg-white/5 border-white/10 text-slate-300 flex items-center gap-2">
                   <span className="text-[10px] uppercase tracking-wider opacity-70 font-semibold">B-Bands</span>
                   <span className="text-xs font-bold">{indicators.bb_position.replace('_', ' ')}</span>
                </div>
              )}

              {indicators.ichimoku_signal && (
                <div className={`px-3 py-1.5 rounded-lg border flex items-center gap-2 ${indicators.ichimoku_signal.includes('BUY') ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : indicators.ichimoku_signal.includes('SELL') ? 'bg-red-500/10 border-red-500/20 text-red-400' : 'bg-white/5 border-white/10 text-slate-300'}`}>
                   <span className="text-[10px] uppercase tracking-wider opacity-70 font-semibold">Ichimoku</span>
                   <span className="text-xs font-bold">{indicators.ichimoku_signal.replace('_', ' ')}</span>
                </div>
              )}
            </div>
          )}

          {/* AI Chart Reading */}
          {deepSettled && (deep?.reasoning || deep?.indicators) && (
            <div className="mt-6 relative rounded-2xl border border-violet-500/20 bg-gradient-to-br from-violet-500/10 to-transparent p-5 overflow-hidden shadow-lg shadow-violet-900/10">
              <div className="absolute -top-10 -right-10 w-32 h-32 bg-violet-500/20 rounded-full blur-3xl pointer-events-none" />
              <div className="flex items-center gap-3 mb-5">
                <div className="w-8 h-8 rounded-lg bg-violet-500/20 flex items-center justify-center">
                  <Activity size={16} className="text-violet-400" />
                </div>
                <div>
                  <div className="text-violet-400 text-sm font-bold tracking-wide">AI CHART READING</div>
                  <div className="text-muted text-[10px] font-mono">{deep.data_source || 'yfinance'} · {deep.as_of?.slice(0,10) || 'today'}</div>
                </div>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 relative z-10">
                {/* Reasoning */}
                <div>
                  <div className="text-xs text-slate-400 uppercase tracking-widest font-semibold mb-3 border-b border-white/5 pb-2">What the chart is showing</div>
                  <div className="space-y-2.5">
                    {[
                      ...(deep.reasoning?.bullish || []).slice(0, 3).map(b => ({ text: b, type: 'bull' })),
                      ...(deep.reasoning?.bearish || []).slice(0, 2).map(b => ({ text: b, type: 'bear' })),
                      ...(deep.reasoning?.neutral || []).slice(0, 1).map(b => ({ text: b, type: 'neut' })),
                    ].map((item, i) => (
                      <div key={i} className="flex gap-3 text-sm items-start bg-black/20 p-2.5 rounded-lg border border-white/5">
                        <span className={`shrink-0 mt-0.5 text-[10px] ${item.type === 'bull' ? 'text-emerald-400' : item.type === 'bear' ? 'text-red-400' : 'text-slate-400'}`}>
                          {item.type === 'bull' ? '▲' : item.type === 'bear' ? '▼' : '◦'}
                        </span>
                        <span className="text-slate-300 leading-snug">{item.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
                
                {/* Key Levels */}
                <div>
                  <div className="text-xs text-slate-400 uppercase tracking-widest font-semibold mb-3 border-b border-white/5 pb-2">Key price levels</div>
                  <div className="space-y-2 text-sm bg-black/20 p-4 rounded-xl border border-white/5">
                    {ts.target_2 && (
                      <div className="flex justify-between items-center">
                        <span className="text-slate-400">Target 2</span>
                        <span className="text-emerald-400 font-mono font-bold">₹{fmt(ts.target_2)}</span>
                      </div>
                    )}
                    {ts.target_1 && (
                      <div className="flex justify-between items-center">
                        <span className="text-slate-400">Target 1</span>
                        <span className="text-emerald-400 font-mono font-bold">₹{fmt(ts.target_1)}</span>
                      </div>
                    )}
                    {indicators.supertrend && (
                      <div className="flex justify-between items-center">
                        <span className="text-slate-400">Supertrend</span>
                        <span className={`font-mono font-bold ${indicators.supertrend_dir === 'BEARISH' ? 'text-red-400' : 'text-emerald-400'}`}>
                          ₹{fmt(indicators.supertrend)} <span className="text-[10px] tracking-widest text-slate-500">({indicators.supertrend_dir})</span>
                        </span>
                      </div>
                    )}
                    {ltp && (
                      <div className="flex justify-between items-center border-t border-white/10 pt-2 mt-2">
                        <span className="text-slate-300 font-semibold">CMP</span>
                        <span className="text-white font-mono font-bold text-base">₹{fmt(ltp)}</span>
                      </div>
                    )}
                    {indicators.ema_50 && (
                      <div className="flex justify-between items-center pt-2">
                        <span className="text-slate-400">EMA 50</span>
                        <span className={`font-mono ${ltp && ltp > indicators.ema_50 ? 'text-emerald-400' : 'text-red-400'}`}>₹{fmt(indicators.ema_50)}</span>
                      </div>
                    )}
                    {ts.stop_loss && (
                      <div className="flex justify-between items-center">
                        <span className="text-slate-400">Stop Loss</span>
                        <span className="text-red-400 font-mono font-bold">₹{fmt(ts.stop_loss)}</span>
                      </div>
                    )}
                    {indicators.bb_lower && (
                      <div className="flex justify-between items-center">
                        <span className="text-slate-400">BB Lower</span>
                        <span className="text-slate-500 font-mono">₹{fmt(indicators.bb_lower)}</span>
                      </div>
                    )}
                  </div>
                  <div className="mt-4 p-3 bg-accent/5 rounded-lg border border-accent/10 text-xs text-slate-300 leading-relaxed flex gap-3 items-start">
                    <Zap size={14} className="text-accent shrink-0 mt-0.5" />
                    <div>
                      <span className="text-accent font-semibold">Next trigger: </span>
                      {indicators.rsi_signal === 'OVERSOLD'
                        ? `RSI oversold at ${fmt(indicators.rsi, 1)} — watch for reversal candle (hammer/engulfing) for bounce entry`
                        : indicators.bb_position === 'BELOW_LOWER'
                        ? `Price below lower Bollinger Band ₹${fmt(indicators.bb_lower)} — statistically extreme, mean reversion probable`
                        : indicators.ichimoku_signal === 'STRONG_BUY'
                        ? 'Ichimoku fully bullish — all 5 components aligned. Breakout above Supertrend would confirm trend change'
                        : indicators.macd_cross === 'BULLISH_CROSS'
                        ? 'MACD bullish crossover confirmed — momentum turning positive'
                        : `Watch ₹${fmt(ts.resistance || ts.target_1)} resistance for breakout confirmation`
                      }
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 5 — AI RESEARCH REPORT
      ═══════════════════════════════════════════════════════════════ */}
      <section className="px-5 pb-6" style={{ background: '#080D1A' }}>
        <SectionLabel>Section 5 · AI equity research</SectionLabel>

        {/* Tavily web research note — shown above LLM analysis if available */}
        {deepSettled && deep?.research_note && (
          <div className="rounded-xl border border-cyan/20 p-4 mb-4" style={{ background: 'rgba(6,182,212,0.04)' }}>
            <div className="flex items-center gap-2 mb-2">
              <Zap size={12} className="text-cyan" />
              <span className="text-cyan text-[10px] font-bold uppercase tracking-wider">Live Web Research</span>
              <span className="ml-auto text-muted text-[10px]">Tavily · scraped now</span>
            </div>
            <p className="text-slate-300 text-sm leading-relaxed">{deep.research_note}</p>
            {(deep.research_headlines || []).length > 0 && (
              <div className="mt-2 space-y-1">
                {deep.research_headlines.slice(0, 3).map((h, i) => (
                  <div key={i} className="flex gap-1.5 text-[11px] text-muted">
                    <span className="text-cyan shrink-0">›</span><span>{h}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Screener.in fundamentals summary pill row */}
        {deepSettled && deep?.screener_summary && (
          <div className="flex flex-wrap gap-2 mb-4">
            {deep.screener_summary.split(' | ').map((part, i) => (
              <span key={i} className="text-[11px] bg-white/[0.04] border border-border rounded px-2 py-1 text-slate-300 font-mono">{part}</span>
            ))}
          </div>
        )}

        {/* Executive summary — expert Groq analysis */}
        <div className="rounded-2xl border border-blue-500/20 p-5 mb-4 relative overflow-hidden"
          style={{ background: 'linear-gradient(145deg,#131E30,#0F1829)', boxShadow: '0 0 40px -15px rgba(139,92,246,0.3)' }}>
          <div className="flex items-start justify-between mb-3">
            <div>
              <div className="text-violet-400 text-[10px] uppercase tracking-[0.2em] font-bold">Expert Research Analysis</div>
              <div className="text-muted text-[10px] mt-0.5">AI-generated · LLM with Screener + Tavily context · not financial advice</div>
            </div>
            {conf != null && (
              <div className="text-right shrink-0 ml-4">
                <div className="text-[10px] text-muted">Signal</div>
                <div className={`font-mono text-lg font-bold ${signalColor}`}>{isBuy ? 'BUY' : isSell ? 'SELL' : 'HOLD'} · {conf}%</div>
              </div>
            )}
          </div>
          {verdictPending ? (
            <div className="space-y-2"><Skel w="w-full" h="h-4" /><Skel w="w-full" h="h-4" /><Skel w="w-5/6" h="h-4" /></div>
          ) : (deep?.ai_summary || deep?.expert_analysis) ? (
            <div className="space-y-0.5">{renderExpertMarkdown(deep.expert_analysis || deep.ai_summary)}</div>
          ) : !deepSettled ? (
            <div className="flex items-center gap-2 text-muted text-sm">
              <div className="w-3 h-3 rounded-full bg-violet-500/30 animate-pulse" />
              Generating expert analysis (fetching Screener + web research)…
            </div>
          ) : (
            <p className="text-slate-300 text-sm leading-relaxed">
              {isBuy
                ? `${fund?.company_name || display} is showing a ${String(signal||'').toLowerCase().replace('_',' ')} signal with ${conf}% confidence. Technical momentum is ${(comp.technical||0) > 0 ? 'strongly bullish' : 'bearish'}. Sector: ${reasoning.sector_name || 'General'} (${reasoning.sector_mood?.toLowerCase() || 'neutral'}). Market regime: ${reasoning.regime || 'unknown'}.`
                : `${fund?.company_name || display} is under pressure. Technical structure is ${(comp.technical||0) > 0 ? 'recovering' : 'bearish'}. Capital preservation recommended — wait for reversal confirmation before entering.`
              }
            </p>
          )}
        </div>

        {/* News Impact Analysis */}
        {(deep?.news || []).length > 0 && (
          <div className="rounded-xl border border-border p-4 mb-4">
            <div className="text-slate-200 text-sm font-semibold mb-3 flex items-center gap-2">
              <BookOpen size={13} className="text-cyan" /> News & Market Context
              <span className="ml-auto text-muted text-[10px] font-normal">{deep.news.length} recent articles</span>
            </div>
            <div className="space-y-3">
              {(deep.news || []).slice(0, 5).map((n, i) => {
                const hasSummary = n.summary && n.summary.trim().length > 20;
                return (
                  <div key={i} className="flex gap-3">
                    <div className="w-0.5 bg-border rounded-full shrink-0 mt-1" style={{ minHeight: '2rem' }} />
                    <div className="flex-1 min-w-0">
                      <div className="text-slate-200 text-xs font-medium leading-snug">{n.headline}</div>
                      <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                        {n.source && <span className="text-muted text-[10px]">{n.source}</span>}
                        {n.published_at && <span className="text-muted text-[10px] font-mono">{n.published_at.slice(0, 10)}</span>}
                      </div>
                      {hasSummary && (
                        <div className="text-muted text-[11px] mt-1 leading-relaxed">
                          {n.summary.slice(0, 200)}{n.summary.length > 200 ? '…' : ''}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

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
        <DeepTab label="Company" subtitle="About · employees · website · sector" icon={BookOpen}>
          {(companyProfile || fund) ? (
            <div className="space-y-4">
              {/* Business description */}
              {companyProfile?.description && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-1.5">About</div>
                  <p className="text-slate-300 text-sm leading-relaxed">{companyProfile.description}</p>
                </div>
              )}
              {/* Key facts grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                {[
                  ['Company', companyProfile?.company_name || fund?.company_name],
                  ['Industry', companyProfile?.industry],
                  ['Sector', companyProfile?.sector || fund?.sector],
                  ['Exchange', companyProfile?.exchange || 'NSE'],
                  ['Market Cap', fund?.market_cap_cr ? `₹${fmt(fund.market_cap_cr, 0)} Cr` : companyProfile?.market_cap ? `₹${fmt(companyProfile.market_cap / 1e7, 0)} Cr` : null],
                  ['Employees', companyProfile?.employees ? Number(companyProfile.employees).toLocaleString('en-IN') : null],
                  ['Div yield', fund?.dividend_yield != null ? fmt(fund.dividend_yield) + '%' : null],
                  ['Country', companyProfile?.city ? `${companyProfile.city}, ${companyProfile.country}` : companyProfile?.country],
                ].filter(([,v]) => v).map(([k, v]) => (
                  <div key={k}>
                    <span className="text-muted text-xs block">{k}</span>
                    <div className="text-slate-200 text-sm font-medium">{v}</div>
                  </div>
                ))}
              </div>
              {/* Website */}
              {companyProfile?.website && (
                <a href={companyProfile.website} target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-cyan text-xs hover:underline">
                  {companyProfile.website}
                </a>
              )}
            </div>
          ) : (
            <div className="text-muted text-sm">{fundLoading ? 'Loading company data…' : 'Company details not available.'}</div>
          )}
        </DeepTab>

        {/* Financials */}
        <DeepTab label="Financials" subtitle="PE · PB · ROE · ROCE · P&L · Balance Sheet" icon={BarChart2}>
          <div className="space-y-5">
            {/* Key ratios */}
            {fund && (
              <div>
                <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Key Ratios</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {[
                    ['PE', fund.pe_ratio != null ? fmt(fund.pe_ratio) : null, fund.pe_ratio > 40 ? 'text-amber-400' : 'text-slate-100'],
                    ['PB', fund.pb_ratio != null ? fmt(fund.pb_ratio) : null, 'text-slate-100'],
                    ['ROE', fund.roe != null ? fmt(fund.roe) + '%' : null, fund.roe > 15 ? 'text-profit' : 'text-amber-400'],
                    ['ROCE', fund.roce != null ? fmt(fund.roce) + '%' : null, fund.roce > 15 ? 'text-profit' : 'text-amber-400'],
                    ['D/E', fund.debt_to_equity != null ? fmt(fund.debt_to_equity, 2) : null, fund.debt_to_equity > 1 ? 'text-loss' : 'text-profit'],
                    ['Curr. Ratio', fund.current_ratio != null ? fmt(fund.current_ratio, 2) : null, 'text-slate-100'],
                    ['Rev CAGR 3y', fund.revenue_growth_3yr != null ? pct(fund.revenue_growth_3yr) : null, fund.revenue_growth_3yr > 0 ? 'text-profit' : 'text-loss'],
                    ['Profit CAGR 3y', fund.profit_growth_3yr != null ? pct(fund.profit_growth_3yr) : null, fund.profit_growth_3yr > 0 ? 'text-profit' : 'text-loss'],
                  ].filter(([,v]) => v != null).map(([k, v, c]) => (
                    <div key={k} className="bg-surface rounded-lg border border-border p-3">
                      <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                      <div className={`font-mono text-base font-bold mt-1 ${c}`}>{v}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Annual income statement */}
            {financials?.income_stmt && Object.keys(financials.income_stmt).length > 0 && (() => {
              const years = Object.keys(financials.income_stmt).sort().reverse();
              const keyRows = ['Total Revenue', 'Gross Profit', 'Operating Income', 'Net Income', 'EBITDA'];
              return (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Income Statement (₹ Cr)</div>
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border bg-surface">
                          <th className="text-left px-3 py-2 text-muted font-medium">Metric</th>
                          {years.slice(0,4).map(y => <th key={y} className="text-right px-3 py-2 text-muted font-medium">{y.slice(0,4)}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {keyRows.map(row => {
                          const vals = years.slice(0,4).map(y => financials.income_stmt[y]?.[row]);
                          if (vals.every(v => v == null)) return null;
                          return (
                            <tr key={row} className="border-b border-border/50 hover:bg-white/[0.02]">
                              <td className="px-3 py-2 text-slate-300">{row}</td>
                              {vals.map((v, i) => (
                                <td key={i} className={`px-3 py-2 text-right font-mono ${v != null && v < 0 ? 'text-loss' : 'text-slate-200'}`}>
                                  {v != null ? fmt(v, 0) : '—'}
                                </td>
                              ))}
                            </tr>
                          );
                        }).filter(Boolean)}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}

            {/* Balance sheet */}
            {financials?.balance_sheet && Object.keys(financials.balance_sheet).length > 0 && (() => {
              const years = Object.keys(financials.balance_sheet).sort().reverse();
              const keyRows = ['Total Assets', 'Total Liabilities Net Minority Interest', 'Stockholders Equity', 'Cash And Cash Equivalents', 'Total Debt'];
              return (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Balance Sheet (₹ Cr)</div>
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border bg-surface">
                          <th className="text-left px-3 py-2 text-muted font-medium">Item</th>
                          {years.slice(0,4).map(y => <th key={y} className="text-right px-3 py-2 text-muted font-medium">{y.slice(0,4)}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {keyRows.map(row => {
                          const vals = years.slice(0,4).map(y => financials.balance_sheet[y]?.[row]);
                          if (vals.every(v => v == null)) return null;
                          return (
                            <tr key={row} className="border-b border-border/50 hover:bg-white/[0.02]">
                              <td className="px-3 py-2 text-slate-300">{row.replace('Net Minority Interest','').replace('And Cash Equivalents','')}</td>
                              {vals.map((v, i) => (
                                <td key={i} className="px-3 py-2 text-right font-mono text-slate-200">
                                  {v != null ? fmt(v, 0) : '—'}
                                </td>
                              ))}
                            </tr>
                          );
                        }).filter(Boolean)}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}

            {!fund && !financials && (
              <div className="text-muted text-sm">{fundLoading ? 'Loading financials…' : 'Financials not available for this symbol.'}</div>
            )}
          </div>
        </DeepTab>

        {/* Ownership */}
        <DeepTab label="Ownership & Smart money" subtitle="Promoter · FII · DII · pledge" icon={Users}>
          {fund ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { k: 'Promoter', v: fund.promoter_holding, suffix: '%', color: fund.promoter_holding > 50 ? 'text-profit' : fund.promoter_holding > 30 ? 'text-slate-200' : 'text-amber-400', note: fund.promoter_holding > 50 ? 'Strong control' : 'Moderate' },
                  { k: 'FII / Foreign', v: fund.fii_holding, suffix: '%', color: 'text-cyan', note: fund.fii_holding > 20 ? 'High FII interest' : '' },
                  { k: 'Pledged', v: fund.pledged_pct, suffix: '%', color: fund.pledged_pct > 10 ? 'text-loss' : fund.pledged_pct > 0 ? 'text-amber-400' : 'text-profit', note: fund.pledged_pct > 10 ? '⚠ High pledge risk' : fund.pledged_pct > 0 ? 'Moderate pledge' : 'No pledge' },
                  { k: 'Fund Score', v: fund.fundamental_score, suffix: '/100', color: fund.fundamental_score > 60 ? 'text-profit' : fund.fundamental_score > 40 ? 'text-amber-400' : 'text-loss', note: fund.fundamental_score > 60 ? 'Strong' : fund.fundamental_score > 40 ? 'Average' : 'Weak' },
                ].filter(x => x.v != null).map(({ k, v, suffix, color, note }) => (
                  <div key={k} className="bg-surface rounded-lg border border-border p-3">
                    <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                    <div className={`font-mono text-base font-bold mt-1 ${color}`}>{fmt(v)}{suffix}</div>
                    {note && <div className="text-[10px] text-muted mt-1">{note}</div>}
                  </div>
                ))}
              </div>
              {/* Visual holding bar */}
              {(fund.promoter_holding != null || fund.fii_holding != null) && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Shareholding Pattern</div>
                  <div className="h-4 rounded-full overflow-hidden flex bg-white/5">
                    {fund.promoter_holding > 0 && <div style={{ width: `${fund.promoter_holding}%` }} className="bg-cyan/70 transition-all" title={`Promoter ${fmt(fund.promoter_holding)}%`} />}
                    {fund.fii_holding > 0 && <div style={{ width: `${fund.fii_holding}%` }} className="bg-violet-400/70 transition-all" title={`FII ${fmt(fund.fii_holding)}%`} />}
                    <div className="flex-1 bg-slate-600/30" title="Retail / DII / Others" />
                  </div>
                  <div className="flex items-center gap-4 mt-1.5 text-[10px] text-muted">
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-cyan/70 inline-block" />Promoter {fmt(fund.promoter_holding)}%</span>
                    {fund.fii_holding > 0 && <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-violet-400/70 inline-block" />FII {fmt(fund.fii_holding)}%</span>}
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-slate-500/70 inline-block" />Others {fmt(Math.max(0, 100 - (fund.promoter_holding||0) - (fund.fii_holding||0)))}%</span>
                  </div>
                </div>
              )}
            </div>
          ) : <div className="text-muted text-sm">{fundLoading ? 'Loading ownership data…' : 'Ownership data not available for this symbol.'}</div>}
        </DeepTab>

        {/* Screener.in Deep Data */}
        <DeepTab label="Screener.in Deep" subtitle="Quarterly P&L · Balance sheet · Cash flows · Shareholding · Pros/Cons" icon={BarChart2}>
          {screenerLoading ? (
            <div className="text-muted text-sm animate-pulse">Crawling Screener.in + NSE India… (~5 s)</div>
          ) : screenerData ? (() => {
            const sc  = screenerData.screener  || {};
            const nse = screenerData.nse       || {};
            const hr  = sc.header_ratios       || {};
            const pc  = sc.pros_cons           || {};
            const qt  = sc.quarterly           || {};
            const ap  = sc.annual_pl           || {};
            const bs  = sc.balance_sheet       || {};
            const cf  = sc.cash_flow           || {};
            const sh  = sc.shareholding        || {};
            const cg  = sc.compounded_growth   || {};
            const eps = sc.annual_eps          || [];
            const nq  = nse.quote              || {};
            const ti  = nse.trade_info         || {};
            const ca  = nse.corporate_actions  || [];
            const fr  = nse.financial_results  || [];
            const bm  = nse.board_meetings     || [];
            const idx = nse.index_membership   || [];

            // Helper: render a generic table from {periods, rows}
            const DataTable = ({ data, title, maxCols = 8 }) => {
              if (!data?.periods?.length || !data?.rows) return null;
              const periods = data.periods.slice(0, maxCols);
              const rows = Object.entries(data.rows).slice(0, 20);
              if (!rows.length) return null;
              return (
                <div>
                  {title && <div className="text-muted text-[10px] uppercase tracking-wider mb-2">{title}</div>}
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs min-w-[500px]">
                      <thead>
                        <tr className="border-b border-border bg-surface">
                          <th className="text-left px-3 py-2 text-muted font-medium sticky left-0 bg-surface min-w-[130px]">Metric</th>
                          {periods.map(p => <th key={p} className="text-right px-2 py-2 text-muted font-medium whitespace-nowrap">{p}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(([name, vals]) => {
                          const vSlice = vals.slice(0, maxCols);
                          if (vSlice.every(v => v == null)) return null;
                          const isProfit = name.toLowerCase().includes('profit') || name.toLowerCase().includes('pat') || name.toLowerCase().includes('net');
                          return (
                            <tr key={name} className="border-b border-border/40 hover:bg-white/[0.02]">
                              <td className="px-3 py-2 text-slate-300 sticky left-0 bg-[#0f1117] whitespace-nowrap">{name}</td>
                              {vSlice.map((v, i) => (
                                <td key={i} className={`px-2 py-2 text-right font-mono ${v != null && v < 0 && isProfit ? 'text-loss' : v != null && v > 0 && isProfit ? 'text-profit' : 'text-slate-200'}`}>
                                  {v != null ? v.toLocaleString('en-IN') : '—'}
                                </td>
                              ))}
                            </tr>
                          );
                        }).filter(Boolean)}
                      </tbody>
                    </table>
                  </div>
                  <div className="text-muted text-[10px] mt-1">Values in ₹ Cr · Source: Screener.in</div>
                </div>
              );
            };

            return (
              <div className="space-y-6">
                {/* NSE Live Quote Banner */}
                {nq.ltp && (
                  <div className="bg-surface rounded-lg border border-border p-4">
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">NSE Live Quote</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                      {[
                        { k: 'LTP', v: nq.ltp != null ? `₹${nq.ltp?.toLocaleString('en-IN')}` : null, c: (nq.change_pct||0) >= 0 ? 'text-profit' : 'text-loss' },
                        { k: 'Change', v: nq.change_pct != null ? `${nq.change_pct > 0 ? '+' : ''}${nq.change_pct}%` : null, c: (nq.change_pct||0) >= 0 ? 'text-profit' : 'text-loss' },
                        { k: 'VWAP', v: nq.vwap != null ? `₹${nq.vwap}` : null, c: 'text-slate-200' },
                        { k: 'Day Range', v: nq.day_low && nq.day_high ? `₹${nq.day_low} – ₹${nq.day_high}` : null, c: 'text-slate-200' },
                        { k: '52W High', v: nq.week52_high != null ? `₹${nq.week52_high}` : null, c: 'text-profit' },
                        { k: '52W Low', v: nq.week52_low != null ? `₹${nq.week52_low}` : null, c: 'text-loss' },
                        { k: 'Upper Circuit', v: nq.upper_circuit != null ? `₹${nq.upper_circuit}` : null, c: 'text-amber-400' },
                        { k: 'Lower Circuit', v: nq.lower_circuit != null ? `₹${nq.lower_circuit}` : null, c: 'text-amber-400' },
                        { k: 'Face Value', v: nq.face_value != null ? `₹${nq.face_value}` : null, c: 'text-slate-200' },
                        { k: 'Lot Size', v: nq.market_lot, c: 'text-slate-200' },
                        { k: 'Vol (today)', v: nq.total_traded_qty != null ? nq.total_traded_qty.toLocaleString('en-IN') : null, c: 'text-slate-200' },
                        { k: 'Traded Val', v: nq.total_traded_val != null ? `₹${nq.total_traded_val.toLocaleString('en-IN')} Cr` : null, c: 'text-slate-200' },
                      ].filter(x => x.v != null).map(({ k, v, c }) => (
                        <div key={k} className="bg-[#0f1117] rounded border border-border/50 p-2">
                          <div className="text-muted text-[10px]">{k}</div>
                          <div className={`font-mono text-sm font-semibold mt-0.5 ${c}`}>{v}</div>
                        </div>
                      ))}
                    </div>
                    {idx.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <span className="text-muted text-[10px]">Index member:</span>
                        {idx.slice(0,6).map(i => (
                          <span key={i} className="text-[10px] text-cyan bg-cyan/10 border border-cyan/20 px-2 py-0.5 rounded">{i}</span>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Delivery % trend */}
                {ti.delivery_last5?.length > 0 && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Delivery % (last 5 sessions) — NSE</div>
                    <div className="grid grid-cols-5 gap-2">
                      {ti.delivery_last5.map((d, i) => (
                        <div key={i} className="bg-surface rounded border border-border p-2 text-center">
                          <div className="text-muted text-[10px] truncate">{d.date?.slice(0,6) || `Day-${i+1}`}</div>
                          <div className={`font-mono text-sm font-bold mt-0.5 ${(d.delivery_pct||0) > 50 ? 'text-profit' : (d.delivery_pct||0) > 30 ? 'text-amber-400' : 'text-loss'}`}>
                            {d.delivery_pct != null ? `${d.delivery_pct}%` : '—'}
                          </div>
                          <div className="text-muted text-[9px]">{d.qty != null ? (d.qty/1e5).toFixed(1)+'L' : ''}</div>
                        </div>
                      ))}
                    </div>
                    {ti.delivery_pct_avg != null && (
                      <div className="text-muted text-[11px] mt-1">5-day avg delivery: <span className={`font-semibold ${ti.delivery_pct_avg > 50 ? 'text-profit' : 'text-amber-400'}`}>{ti.delivery_pct_avg}%</span></div>
                    )}
                  </div>
                )}

                {/* Screener Key Ratios */}
                {Object.keys(hr).length > 0 && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Key Ratios · Screener.in</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {[
                        { k: 'Mkt Cap', v: hr.market_cap_cr != null ? `₹${hr.market_cap_cr?.toLocaleString('en-IN')} Cr` : null, c: 'text-slate-200' },
                        { k: 'Current Price', v: hr.current_price != null ? `₹${hr.current_price}` : null, c: 'text-slate-200' },
                        { k: 'PE', v: hr.pe_ratio != null ? `${hr.pe_ratio}×` : null, c: hr.pe_ratio > 40 ? 'text-amber-400' : 'text-profit' },
                        { k: 'PB', v: hr.pb_ratio != null ? `${hr.pb_ratio}×` : null, c: 'text-slate-200' },
                        { k: 'ROE', v: hr.roe != null ? `${hr.roe}%` : null, c: hr.roe > 15 ? 'text-profit' : 'text-amber-400' },
                        { k: 'ROCE', v: hr.roce != null ? `${hr.roce}%` : null, c: hr.roce > 15 ? 'text-profit' : 'text-amber-400' },
                        { k: 'Book Value', v: hr.book_value != null ? `₹${hr.book_value}` : null, c: 'text-slate-200' },
                        { k: 'Div Yield', v: hr.dividend_yield != null ? `${hr.dividend_yield}%` : null, c: 'text-cyan' },
                        { k: '52W High', v: hr.high_52w != null ? `₹${hr.high_52w}` : null, c: 'text-profit' },
                        { k: '52W Low', v: hr.low_52w != null ? `₹${hr.low_52w}` : null, c: 'text-loss' },
                        { k: 'Face Value', v: hr.face_value != null ? `₹${hr.face_value}` : null, c: 'text-slate-200' },
                      ].filter(x => x.v != null).map(({ k, v, c }) => (
                        <div key={k} className="bg-surface rounded border border-border/50 p-2">
                          <div className="text-muted text-[10px]">{k}</div>
                          <div className={`font-mono text-sm font-semibold mt-0.5 ${c}`}>{v}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Pros & Cons */}
                {(pc.pros?.length > 0 || pc.cons?.length > 0) && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {pc.pros?.length > 0 && (
                      <div className="bg-profit/5 border border-profit/20 rounded-lg p-4">
                        <div className="text-profit text-xs font-semibold mb-2">✅ Pros</div>
                        <ul className="space-y-1.5">
                          {pc.pros.map((p, i) => <li key={i} className="text-slate-300 text-xs">• {p}</li>)}
                        </ul>
                      </div>
                    )}
                    {pc.cons?.length > 0 && (
                      <div className="bg-loss/5 border border-loss/20 rounded-lg p-4">
                        <div className="text-loss text-xs font-semibold mb-2">⚠️ Cons</div>
                        <ul className="space-y-1.5">
                          {pc.cons.map((c, i) => <li key={i} className="text-slate-300 text-xs">• {c}</li>)}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* Compounded Growth Rates */}
                {Object.keys(cg).length > 0 && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Compounded Growth Rates</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {Object.entries(cg).map(([k, v]) => (
                        <div key={k} className="bg-surface rounded border border-border/50 p-2">
                          <div className="text-muted text-[10px] capitalize">{k}</div>
                          <div className={`font-mono text-sm font-semibold mt-0.5 ${v > 15 ? 'text-profit' : v > 0 ? 'text-amber-400' : 'text-loss'}`}>{v}%</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Quarterly Results */}
                <DataTable data={qt} title="Quarterly Results (₹ Cr)" maxCols={8} />

                {/* Annual P&L */}
                <DataTable data={ap} title="Annual P&L (₹ Cr)" maxCols={10} />

                {/* Balance Sheet */}
                <DataTable data={bs} title="Balance Sheet (₹ Cr)" maxCols={10} />

                {/* Cash Flow */}
                <DataTable data={cf} title="Cash Flow (₹ Cr)" maxCols={10} />

                {/* Shareholding Pattern Trend */}
                {sh.shareholding_trend && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Shareholding Pattern Trend</div>
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-xs min-w-[400px]">
                        <thead>
                          <tr className="border-b border-border bg-surface">
                            <th className="text-left px-3 py-2 text-muted">Holder</th>
                            {(sh.periods || []).slice(0,8).map(p => (
                              <th key={p} className="text-right px-2 py-2 text-muted whitespace-nowrap">{p}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(sh.shareholding_trend.rows || {}).map(([name, vals]) => {
                            const isPromoter = name.toLowerCase().includes('promoter');
                            const isFII = name.toLowerCase().includes('fii') || name.toLowerCase().includes('foreign');
                            const c = isPromoter ? 'text-cyan' : isFII ? 'text-violet-400' : 'text-slate-200';
                            return (
                              <tr key={name} className="border-b border-border/40 hover:bg-white/[0.02]">
                                <td className={`px-3 py-2 font-medium ${c}`}>{name}</td>
                                {vals.slice(0,8).map((v, i) => (
                                  <td key={i} className={`px-2 py-2 text-right font-mono ${c}`}>
                                    {v != null ? `${v}%` : '—'}
                                  </td>
                                ))}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* NSE Financial Results */}
                {fr.length > 0 && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Financial Results — NSE Filings</div>
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-border bg-surface">
                            <th className="text-left px-3 py-2 text-muted">Period</th>
                            <th className="text-right px-3 py-2 text-muted">Sales ₹Cr</th>
                            <th className="text-right px-3 py-2 text-muted">Net Profit ₹Cr</th>
                            <th className="text-right px-3 py-2 text-muted">EPS</th>
                            <th className="text-left px-3 py-2 text-muted">Type</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fr.map((r, i) => (
                            <tr key={i} className="border-b border-border/40 hover:bg-white/[0.02]">
                              <td className="px-3 py-2 text-slate-300">{r.period}</td>
                              <td className="px-3 py-2 text-right font-mono text-slate-200">{r.sales != null ? r.sales.toLocaleString('en-IN') : '—'}</td>
                              <td className={`px-3 py-2 text-right font-mono ${(r.net_profit||0) >= 0 ? 'text-profit' : 'text-loss'}`}>{r.net_profit != null ? r.net_profit.toLocaleString('en-IN') : '—'}</td>
                              <td className="px-3 py-2 text-right font-mono text-slate-200">{r.eps != null ? r.eps : '—'}</td>
                              <td className="px-3 py-2 text-muted text-[10px]">{r.result_type}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Corporate Actions */}
                {ca.length > 0 && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Corporate Actions — NSE</div>
                    <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                      {ca.slice(0,10).map((a, i) => (
                        <div key={i} className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/[0.02]">
                          <span className="text-muted text-[11px] w-20 shrink-0">{a.ex_date}</span>
                          <span className="text-slate-300 text-xs flex-1">{a.purpose}</span>
                          {a.remarks && <span className="text-muted text-[10px]">{a.remarks}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Board Meetings */}
                {bm.length > 0 && (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Board Meetings — NSE</div>
                    <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                      {bm.map((m, i) => (
                        <div key={i} className="flex items-center gap-3 px-4 py-2 hover:bg-white/[0.02]">
                          <span className="text-cyan text-[11px] w-24 shrink-0">{m.meeting_date}</span>
                          <span className="text-slate-300 text-xs">{m.purpose}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Source links */}
                <div className="flex gap-3 pt-1">
                  <a href={`https://www.screener.in/company/${display}/`} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-slate-300 bg-white/5 border border-border px-3 py-1.5 rounded-lg hover:bg-white/10 transition-colors">
                    Screener.in →
                  </a>
                  <a href={`https://www.nseindia.com/get-quotes/equity?symbol=${display}`} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-cyan bg-cyan/5 border border-cyan/20 px-3 py-1.5 rounded-lg hover:bg-cyan/10 transition-colors">
                    NSE India →
                  </a>
                </div>
              </div>
            );
          })() : (
            <div className="text-muted text-sm">
              Screener.in data not available.{' '}
              <a href={`https://www.screener.in/company/${display}/`} target="_blank" rel="noopener noreferrer" className="text-cyan hover:underline">
                Open Screener.in →
              </a>
            </div>
          )}
        </DeepTab>

        {/* Peers */}
        <DeepTab label="Sector Peers" subtitle="Top-ranked stocks in same sector" icon={Users}>
          {peers?.peers?.length > 0 ? (
            <div className="space-y-3">
              <div className="text-muted text-xs mb-2">Sector: <span className="text-slate-300">{peers.sector}</span> · top ranked from market scanner</div>
              <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                {peers.peers.map((p, i) => {
                  const pb = String(p.signal||'').includes('BUY');
                  const ps = String(p.signal||'').includes('SELL');
                  return (
                    <Link key={p.symbol} to={`/s/${p.symbol}`}
                      className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/[0.03] transition-colors">
                      <span className="text-xs text-muted w-5 text-right">{i+1}</span>
                      <span className="font-mono text-sm text-slate-200 flex-1">{p.symbol}</span>
                      <span className={`text-xs font-bold px-1.5 py-0.5 rounded border ${pb ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' : ps ? 'text-red-400 bg-red-500/10 border-red-500/20' : 'text-amber-400 bg-amber-500/10 border-amber-500/20'}`}>
                        {p.signal?.replace('_',' ') || 'HOLD'}
                      </span>
                      <span className="text-xs font-mono text-muted w-12 text-right">{p.score}</span>
                      {p.upper_circuit_days > 0 && <span className="text-amber-400 text-[10px]">UC{p.upper_circuit_days}D</span>}
                    </Link>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="text-muted text-sm">{peers === null ? 'Loading peers…' : 'No sector peers found in current market scan.'}</div>
          )}
        </DeepTab>

        {/* Technicals advanced */}
        <DeepTab label="Technicals (Advanced)" subtitle="Full indicator dashboard · EMA · MACD · ADX · BB" icon={Activity}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
            {[
              ['RSI', indicators.rsi, indicators.rsi_signal === 'OVERBOUGHT' ? 'text-loss' : indicators.rsi_signal === 'OVERSOLD' ? 'text-profit' : 'text-slate-200'],
              ['MACD', indicators.macd, (indicators.macd||0) > 0 ? 'text-profit' : 'text-loss'],
              ['MACD Signal', indicators.macd_signal, 'text-slate-200'],
              ['EMA 20', indicators.ema_20, 'text-slate-200'],
              ['EMA 50', indicators.ema_50, 'text-slate-200'],
              ['EMA 200', indicators.ema_200, 'text-slate-200'],
              ['ADX', indicators.adx, (indicators.adx||0) > 25 ? 'text-profit' : 'text-muted'],
              ['VWAP', indicators.vwap, 'text-slate-200'],
              ['BB Upper', indicators.bb_upper, 'text-slate-200'],
              ['BB Lower', indicators.bb_lower, 'text-slate-200'],
              ['Stoch K', indicators.stoch_k, 'text-slate-200'],
              ['Stoch D', indicators.stoch_d, 'text-slate-200'],
            ].filter(([,v]) => v != null).map(([k, v, c]) => (
              <div key={k} className="bg-surface rounded-lg border border-border p-3">
                <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                <div className={`font-mono text-sm font-semibold mt-1 ${c}`}>{fmt(v)}</div>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-3 text-[11px] text-muted">
            {indicators.ema_trend && <span>EMA trend: <span className="text-slate-300">{indicators.ema_trend}</span></span>}
            {indicators.macd_cross && <span>MACD: <span className={indicators.macd_cross === 'BULLISH_CROSS' ? 'text-profit' : 'text-loss'}>{indicators.macd_cross.replace('_',' ')}</span></span>}
            {indicators.supertrend_dir && <span>Supertrend: <span className={indicators.supertrend_dir === 'UP' ? 'text-profit' : 'text-loss'}>{indicators.supertrend_dir}</span></span>}
            {indicators.adx_strength && <span>ADX: <span className="text-slate-300">{indicators.adx_strength}</span></span>}
          </div>
          <p className="text-muted text-xs mt-3">
            Open the <Link to={`/chart?symbol=${nsSymbol}&name=${display}`} className="text-cyan hover:underline">full chart page</Link> for drawing tools, Fibonacci levels, and pattern detection.
          </p>
        </DeepTab>

        {/* Options */}
        <DeepTab label="Options & F&O" subtitle="PCR · Max Pain · F&O eligibility · Hub options score" icon={PieChart}>
          <div className="space-y-4">
            {/* F&O Eligibility Status — authoritative (NFO instrument master),
                with a market-cap heuristic only when the master is unavailable. */}
            {(() => {
              const mcap = fund?.market_cap_cr ?? (companyProfile?.market_cap != null ? companyProfile.market_cap / 1e7 : null);
              // is_fno from the NFO master is truth; null = master unavailable → fall back to mcap.
              const eligible = fnoStatus?.is_fno != null
                ? fnoStatus.is_fno
                : (mcap != null ? mcap > 5000 : null);
              const authoritative = fnoStatus?.is_fno != null;
              return (
                <div className={`flex items-start gap-3 p-3 rounded-lg border ${eligible === true ? 'bg-profit/5 border-profit/20' : eligible === false ? 'bg-amber-500/5 border-amber-500/20' : 'bg-white/[0.02] border-border'}`}>
                  <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${eligible === true ? 'bg-profit' : eligible === false ? 'bg-amber-400' : 'bg-slate-500'}`} />
                  <div className="flex-1 min-w-0">
                    <div className={`text-sm font-semibold ${eligible === true ? 'text-profit' : eligible === false ? 'text-amber-400' : 'text-slate-300'}`}>
                      {eligible === true ? `${display} is in NSE F&O` : eligible === false ? `${display} is NOT in NSE F&O` : `${display} F&O Status`}
                    </div>
                    <div className="text-muted text-xs mt-1 leading-relaxed">
                      {eligible === true
                        ? (authoritative
                            ? `${display} has listed equity derivatives (Futures + Options) on NSE. View live chain on NSE India or Sensibull.`
                            : `Market cap ₹${fmt(mcap, 0)} Cr clears the ₹5,000 Cr threshold (heuristic — F&O list unavailable). Verify on NSE India.`)
                        : eligible === false
                        ? (authoritative
                            ? `${display} has no equity derivatives on NSE${mcap != null ? ` (market cap ₹${fmt(mcap, 0)} Cr)` : ''} — F&O eligibility needs sustained liquidity/turnover, not just market cap, so the hub uses the index-wide NIFTY PCR for it.`
                            : `Market cap ₹${fmt(mcap, 0)} Cr is below the ₹5,000 Cr threshold. Verify on NSE India.`)
                        : 'Checking the NSE F&O instrument master…'}
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Hub Options Score explanation */}
            <div>
              <div className="text-muted text-[10px] uppercase tracking-wider mb-2">How the Hub Uses Options Data</div>
              <p className="text-slate-300 text-xs leading-relaxed mb-3">
                The Hub's options factor (5% weight) uses each stock's <strong className="text-slate-200">own Put-Call Ratio &amp; IV-skew</strong> when the per-stock F&amp;O enrichment job has recent data for it
                {fnoStatus?.is_fno === false ? <> — but {display} isn't in NSE F&amp;O, so it</> : !fnoStatus?.has_options_data ? <> — none is loaded for {display} yet, so it</> : ', otherwise it'} falls back to the market-wide <strong className="text-slate-200">NIFTY PCR</strong>.
                PCR &gt; 1.3 = heavy hedging = contrarian bullish. PCR &lt; 0.7 = complacency = caution.
              </p>
              {comp.options != null && (
                <div className="flex items-center gap-4">
                  <div className="bg-surface rounded-lg border border-border p-3 text-center">
                    <div className="text-muted text-[10px] uppercase tracking-wider">Options Score</div>
                    <div className={`font-mono text-lg font-bold mt-1 ${comp.options >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {comp.options > 0 ? '+' : ''}{Math.round(comp.options)}
                    </div>
                    <div className="text-muted text-[10px] mt-0.5">out of ±15</div>
                  </div>
                  <div className="text-muted text-xs flex-1 leading-relaxed">
                    Score {comp.options >= 0 ? '≥ 0' : '< 0'} = Nifty PCR is {comp.options > 10 ? 'elevated (>1.3) — contrarian bullish signal' : comp.options < -10 ? 'low (<0.7) — complacency warning' : 'neutral (0.7–1.3) — no strong signal'}.
                    This is an index-level indicator — it applies equally to all F&O and non-F&O stocks.
                  </div>
                </div>
              )}
            </div>

            {/* Live option chain (Kite) — reliable structured data */}
            {optionsChain?.available && (
              <div className="bg-card border border-cyan/20 rounded-lg p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-slate-200 text-sm font-semibold">Live Option Chain</div>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan/10 border border-cyan/20 text-cyan">
                    Kite · {optionsChain.expiry_date}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-surface rounded border border-border p-3 text-center">
                    <div className="text-muted text-[10px] uppercase tracking-wider">PCR</div>
                    <div className={`font-mono text-base font-bold mt-1 ${optionsChain.pcr > 1.2 ? 'text-profit' : optionsChain.pcr < 0.8 ? 'text-loss' : 'text-amber-400'}`}>
                      {optionsChain.pcr}
                    </div>
                    <div className="text-muted text-[9px] mt-0.5">
                      {optionsChain.pcr > 1.3 ? 'Bullish (heavy hedging)' : optionsChain.pcr < 0.7 ? 'Bearish (complacency)' : 'Neutral'}
                    </div>
                  </div>
                  <div className="bg-surface rounded border border-border p-3 text-center">
                    <div className="text-muted text-[10px] uppercase tracking-wider">Max Pain</div>
                    <div className="font-mono text-base font-bold mt-1 text-slate-200">₹{optionsChain.max_pain?.toLocaleString('en-IN')}</div>
                    <div className="text-muted text-[9px] mt-0.5">Spot ₹{optionsChain.spot?.toLocaleString('en-IN')}</div>
                  </div>
                  <div className="bg-surface rounded border border-border p-3 text-center">
                    <div className="text-muted text-[10px] uppercase tracking-wider">ATM IV</div>
                    <div className={`font-mono text-base font-bold mt-1 ${optionsChain.iv > 40 ? 'text-loss' : 'text-amber-400'}`}>
                      {optionsChain.iv != null ? `${optionsChain.iv}%` : '—'}
                    </div>
                    <div className="text-muted text-[9px] mt-0.5">{optionsChain.iv > 40 ? 'Elevated' : 'Normal'}</div>
                  </div>
                </div>
                {(optionsChain.support?.length > 0 || optionsChain.resistance?.length > 0) && (
                  <div className="flex flex-wrap gap-2">
                    {optionsChain.resistance?.map((s, i) => (
                      <span key={`r${i}`} className="text-[11px] font-mono px-2.5 py-1 rounded border text-loss bg-loss/10 border-loss/20">R ₹{s?.toLocaleString('en-IN')}</span>
                    ))}
                    {optionsChain.support?.map((s, i) => (
                      <span key={`s${i}`} className="text-[11px] font-mono px-2.5 py-1 rounded border text-profit bg-profit/10 border-profit/20">S ₹{s?.toLocaleString('en-IN')}</span>
                    ))}
                  </div>
                )}
                <p className="text-muted text-[10px] leading-relaxed">Near-ATM open interest from the live NSE chain. This data also feeds the Hub's per-stock options score.</p>
              </div>
            )}

            {/* Agent-driven options research (web narrative) */}
            <div className="bg-card border border-border rounded-lg p-4 space-y-4">
              <div className="flex items-center justify-between">
                <div className="text-slate-200 text-sm font-semibold">🤖 Agent Options Research</div>
                {optionsResearchLoading && (
                  <span className="text-muted text-[10px] animate-pulse">Searching web…</span>
                )}
              </div>

              {optionsResearch && !optionsResearchLoading ? (
                <div className="space-y-4">
                  {/* Key metrics row */}
                  {(optionsResearch.pcr != null || optionsResearch.max_pain != null || optionsResearch.iv != null) && (
                    <div className="grid grid-cols-3 gap-3">
                      {optionsResearch.pcr != null && (
                        <div className="bg-surface rounded border border-border p-3 text-center">
                          <div className="text-muted text-[10px] uppercase tracking-wider">PCR</div>
                          <div className={`font-mono text-base font-bold mt-1 ${optionsResearch.pcr > 1.2 ? 'text-profit' : optionsResearch.pcr < 0.8 ? 'text-loss' : 'text-amber-400'}`}>
                            {optionsResearch.pcr}
                          </div>
                          <div className="text-muted text-[9px] mt-0.5">
                            {optionsResearch.pcr > 1.3 ? 'Bullish (heavy hedging)' : optionsResearch.pcr < 0.7 ? 'Bearish (complacency)' : 'Neutral'}
                          </div>
                        </div>
                      )}
                      {optionsResearch.max_pain != null && (
                        <div className="bg-surface rounded border border-border p-3 text-center">
                          <div className="text-muted text-[10px] uppercase tracking-wider">Max Pain</div>
                          <div className="font-mono text-base font-bold mt-1 text-slate-200">₹{optionsResearch.max_pain?.toLocaleString('en-IN')}</div>
                          <div className="text-muted text-[9px] mt-0.5">Options expiry gravity</div>
                        </div>
                      )}
                      {optionsResearch.iv != null && (
                        <div className="bg-surface rounded border border-border p-3 text-center">
                          <div className="text-muted text-[10px] uppercase tracking-wider">Impl. Vol</div>
                          <div className={`font-mono text-base font-bold mt-1 ${optionsResearch.iv > 40 ? 'text-loss' : 'text-amber-400'}`}>
                            {optionsResearch.iv}%
                          </div>
                          <div className="text-muted text-[9px] mt-0.5">{optionsResearch.iv > 40 ? 'Elevated IV' : 'Normal range'}</div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Key strikes */}
                  {optionsResearch.key_strikes?.length > 0 && (
                    <div>
                      <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Key Strikes Found</div>
                      <div className="flex flex-wrap gap-2">
                        {optionsResearch.key_strikes.map((s, i) => (
                          <span key={i} className={`text-[11px] font-mono px-2.5 py-1 rounded border ${s.type === 'CALL' || s.type === 'CE' ? 'text-profit bg-profit/10 border-profit/20' : 'text-loss bg-loss/10 border-loss/20'}`}>
                            {s.type} ₹{s.strike?.toLocaleString('en-IN')}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Agent synthesis */}
                  {optionsResearch.agent_note && (
                    <div className="bg-violet-500/5 border border-violet-500/20 rounded-lg p-3">
                      <div className="text-violet-400 text-[10px] uppercase tracking-wider mb-1">🤖 AI Synthesis</div>
                      <p className="text-slate-300 text-xs leading-relaxed">{optionsResearch.agent_note}</p>
                    </div>
                  )}

                  {/* Summary from web */}
                  {optionsResearch.summary && (
                    <div>
                      <div className="text-muted text-[10px] uppercase tracking-wider mb-1">Web Research Summary</div>
                      <p className="text-slate-400 text-xs leading-relaxed">{optionsResearch.summary}</p>
                    </div>
                  )}

                  {/* Source URLs the agent found */}
                  {optionsResearch.sources?.length > 0 && (
                    <div>
                      <div className="text-muted text-[10px] uppercase tracking-wider mb-1.5">Sources discovered by agent</div>
                      <div className="space-y-1">
                        {optionsResearch.sources.slice(0, 5).map((url, i) => {
                          const domain = (() => { try { return new URL(url).hostname.replace('www.',''); } catch { return url; } })();
                          return (
                            <a key={i} href={url} target="_blank" rel="noopener noreferrer"
                              className="flex items-center gap-2 text-[11px] text-cyan hover:text-cyan/80 hover:underline truncate">
                              <span className="w-4 h-4 rounded bg-cyan/10 border border-cyan/20 flex items-center justify-center text-[9px] font-bold shrink-0">{i+1}</span>
                              <span className="font-medium text-slate-300">{domain}</span>
                              <span className="text-muted truncate">{url.slice(0, 60)}…</span>
                            </a>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Headlines found */}
                  {optionsResearch.headlines?.length > 0 && (
                    <div>
                      <div className="text-muted text-[10px] uppercase tracking-wider mb-1">Latest Headlines</div>
                      <ul className="space-y-1">
                        {optionsResearch.headlines.slice(0, 4).map((h, i) => (
                          <li key={i} className="text-slate-400 text-[11px]">• {h}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* No structured data found — distinguish "not in F&O" from a
                      web-research miss on a stock that IS in F&O. Prefer the
                      authoritative NFO master; fall back to the mcap heuristic. */}
                  {!optionsResearch.pcr && !optionsResearch.max_pain && !optionsResearch.iv &&
                   !optionsResearch.key_strikes?.length && !optionsResearch.agent_note &&
                   !optionsChain?.available && (() => {
                    const mcap = fund?.market_cap_cr ?? (companyProfile?.market_cap != null ? companyProfile.market_cap / 1e7 : null);
                    const fnoEligible = fnoStatus?.is_fno != null
                      ? fnoStatus.is_fno
                      : (mcap != null ? mcap > 5000 : null);
                    return (
                    <div className="flex items-start gap-3 p-3 rounded-lg bg-amber-500/5 border border-amber-500/20">
                      <ShieldAlert size={14} className="text-amber-400 shrink-0 mt-0.5" />
                      <div>
                        <div className="text-amber-400 text-sm font-semibold">
                          {fnoEligible === false ? 'Not in F&O Segment' : 'Live Options Data Unavailable'}
                        </div>
                        <div className="text-muted text-xs mt-1 leading-relaxed">
                          {fnoEligible === false
                            ? <>{display} does not appear to have equity derivatives (Futures &amp; Options) traded on NSE. F&amp;O eligibility requires market cap &gt; ₹5,000 Cr and minimum average daily turnover.</>
                            : fnoEligible === true
                            ? <>{display} is in NSE F&amp;O{mcap != null ? ` (market cap ₹${fmt(mcap, 0)} Cr)` : ''}, but the web research agent couldn't retrieve a live options chain for it right now. This does not affect the Hub options score, which uses the stock's own PCR/IV when the F&amp;O enrichment job has data (else the index-wide NIFTY PCR).</>
                            : <>The web research agent couldn't retrieve a live options chain for {display} right now. This does not affect the Hub options score, which uses the stock's own PCR/IV when the F&amp;O enrichment job has data (else the index-wide NIFTY PCR).</>}
                        </div>
                        {optionsResearch.headlines?.length > 0 && (
                          <ul className="mt-2 space-y-1">
                            {optionsResearch.headlines.slice(0, 3).map((h, i) => (
                              <li key={i} className="text-slate-400 text-[11px]">• {h}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                    );
                  })()}
                </div>
              ) : optionsResearchLoading ? (
                <div className="text-muted text-xs animate-pulse">
                  Agent is searching the web for {display} F&O data…
                </div>
              ) : (
                <div className="flex items-start gap-3 p-3 rounded-lg bg-white/[0.02] border border-border">
                  <ShieldAlert size={14} className="text-muted shrink-0 mt-0.5" />
                  <div className="text-muted text-xs leading-relaxed">
                    Agent options research not loaded yet — trigger a page refresh or set <code className="text-cyan">TAVILY_API_KEY</code> in <code className="text-cyan">.env</code> to enable live web search.
                  </div>
                </div>
              )}

              {/* NSE official link — always shown */}
              <div className="pt-1 border-t border-border">
                <a href={`https://www.nseindia.com/get-quotes/equity?symbol=${display}`}
                  target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-cyan bg-cyan/5 border border-cyan/20 px-3 py-1.5 rounded-lg hover:bg-cyan/10 transition-colors inline-flex items-center gap-1.5">
                  View on NSE India (official) →
                </a>
              </div>
            </div>
          </div>
        </DeepTab>

        {/* ── Upstox Live Data ────────────────────────────────────────────── */}
        <DeepTab
          label="Upstox Live Data"
          subtitle={upstoxStatus?.authenticated ? "News · Ratios · Shareholding · Corporate Actions" : "Authenticate to unlock"}
          badge={upstoxStatus?.authenticated ? "LIVE" : upstoxStatus?.api_key_set ? "Auth needed" : "Not configured"}
          badgeColor={upstoxStatus?.authenticated ? "text-profit" : "text-amber-400"}
          icon={Zap}
        >
          {/* Auth guard banner */}
          {!upstoxStatus?.authenticated && (
            <div className="mb-4 flex items-start gap-3 p-4 rounded-xl border border-amber-500/30 bg-amber-500/5">
              <ShieldAlert size={16} className="text-amber-400 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div className="text-amber-400 font-semibold text-sm mb-1">
                  {upstoxStatus?.api_key_set
                    ? "Upstox access token not set — authentication required"
                    : "Upstox API not configured"}
                </div>
                <div className="text-muted text-xs leading-relaxed mb-2">
                  {upstoxStatus?.api_key_set
                    ? "Your Upstox API key is configured. Click the button below to open the OAuth login and get your access token. Once authenticated, this tab will show live news, fundamental ratios, detailed shareholding, and corporate actions from Upstox."
                    : "Add UPSTOX_API_KEY and UPSTOX_API_SECRET to your .env file to enable Upstox data enrichment (news, fundamentals, shareholding, corporate actions)."}
                </div>
                {upstoxStatus?.api_key_set && (
                  <a
                    href="/api/v1/upstox/login"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 text-amber-400 px-3 py-1.5 rounded-lg transition-colors"
                  >
                    <Zap size={12} /> Authenticate Upstox →
                  </a>
                )}
              </div>
            </div>
          )}

          {upstoxLoading && (
            <div className="flex items-center gap-2 text-muted text-sm py-4">
              <div className="w-3 h-3 rounded-full border border-cyan/30 border-t-cyan animate-spin" />
              Loading Upstox data…
            </div>
          )}

          {upstoxData && (
            <div className="space-y-6">
              {/* Company Profile */}
              {upstoxData.profile && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Company · Upstox</div>
                  <div className="bg-surface rounded-lg border border-border p-4">
                    {upstoxData.profile.description && (
                      <p className="text-slate-300 text-sm leading-relaxed mb-3">{upstoxData.profile.description}</p>
                    )}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                      {[
                        ['Industry',    upstoxData.profile.industry],
                        ['Sector',      upstoxData.profile.sector],
                        ['Market Cap',  upstoxData.profile.market_cap ? `₹${fmt(upstoxData.profile.market_cap / 1e7, 0)} Cr` : null],
                        ['Employees',   upstoxData.profile.employees ? Number(upstoxData.profile.employees).toLocaleString('en-IN') : null],
                        ['Country',     upstoxData.profile.country],
                        ['Founded',     upstoxData.profile.founded],
                      ].filter(([,v]) => v).map(([k, v]) => (
                        <div key={k}>
                          <div className="text-muted">{k}</div>
                          <div className="text-slate-200 font-medium mt-0.5">{v}</div>
                        </div>
                      ))}
                    </div>
                    {upstoxData.profile.website && (
                      <a href={upstoxData.profile.website} target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-cyan text-xs hover:underline mt-2">
                        {upstoxData.profile.website}
                      </a>
                    )}
                  </div>
                </div>
              )}

              {/* Key Ratios */}
              {upstoxData.key_ratios && Object.keys(upstoxData.key_ratios).length > 0 && (() => {
                const r = upstoxData.key_ratios;
                return (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Key Ratios · Upstox</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {[
                        ['PE',       r.pe_ratio,           r.pe_ratio > 40 ? 'text-amber-400' : 'text-profit'],
                        ['PB',       r.pb_ratio,           'text-slate-200'],
                        ['ROE',      r.roe != null ? fmt(r.roe)+'%' : null,  r.roe > 15 ? 'text-profit' : 'text-amber-400'],
                        ['ROCE',     r.roce != null ? fmt(r.roce)+'%' : null, r.roce > 15 ? 'text-profit' : 'text-amber-400'],
                        ['EPS',      r.eps != null ? `₹${fmt(r.eps)}` : null, 'text-slate-200'],
                        ['D/E',      r.debt_to_equity != null ? fmt(r.debt_to_equity,2) : null, r.debt_to_equity > 1 ? 'text-loss' : 'text-profit'],
                        ['Div Yield',r.dividend_yield != null ? fmt(r.dividend_yield)+'%' : null, 'text-cyan'],
                        ['Beta',     r.beta != null ? fmt(r.beta,2) : null, 'text-slate-200'],
                      ].filter(([,v]) => v != null).map(([k, v, c]) => (
                        <div key={k} className="bg-surface rounded border border-border/50 p-2.5">
                          <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                          <div className={`font-mono text-sm font-bold mt-1 ${c}`}>{v}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {/* Shareholding Pattern */}
              {upstoxData.shareholding && (upstoxData.shareholding.promoter != null || upstoxData.shareholding.fii != null) && (() => {
                const sh = upstoxData.shareholding;
                const promoter = sh.promoter ?? 0;
                const fii      = sh.fii ?? sh.foreign_institutions ?? 0;
                const dii      = sh.dii ?? sh.domestic_institutions ?? 0;
                const pub      = sh.public ?? sh.retail ?? Math.max(0, 100 - promoter - fii - dii);
                return (
                  <div>
                    <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Shareholding Pattern · Upstox</div>
                    <div className="space-y-3">
                      {/* Stacked bar */}
                      <div className="h-5 rounded-lg overflow-hidden flex gap-px bg-white/5">
                        {[
                          { label: 'Promoter', pct: promoter, cls: 'bg-cyan/70' },
                          { label: 'FII',      pct: fii,      cls: 'bg-violet-400/70' },
                          { label: 'DII',      pct: dii,      cls: 'bg-amber-400/70' },
                          { label: 'Public',   pct: pub,      cls: 'bg-slate-500/50' },
                        ].filter(x => x.pct > 0).map(x => (
                          <div key={x.label} style={{ width: `${x.pct}%` }} className={`${x.cls} transition-all`} title={`${x.label} ${fmt(x.pct)}%`} />
                        ))}
                      </div>
                      {/* Legend */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        {[
                          { label: 'Promoter', pct: promoter, dot: 'bg-cyan/70',        note: promoter > 50 ? 'Strong control' : promoter > 30 ? 'Moderate' : 'Low' },
                          { label: 'FII / Foreign', pct: fii, dot: 'bg-violet-400/70', note: fii > 20 ? 'High FII interest' : '' },
                          { label: 'DII / MF',  pct: dii,    dot: 'bg-amber-400/70',   note: '' },
                          { label: 'Public',    pct: pub,    dot: 'bg-slate-500/50',    note: '' },
                        ].map(x => (
                          <div key={x.label} className="bg-surface rounded border border-border/50 p-2.5">
                            <div className="flex items-center gap-1.5 mb-1">
                              <span className={`w-2 h-2 rounded-full shrink-0 ${x.dot}`} />
                              <span className="text-muted text-[10px]">{x.label}</span>
                            </div>
                            <div className="font-mono text-sm font-bold text-slate-200">{fmt(x.pct)}%</div>
                            {x.note && <div className="text-muted text-[10px] mt-0.5">{x.note}</div>}
                          </div>
                        ))}
                      </div>
                      {sh.as_of && <div className="text-muted text-[10px]">As of: {sh.as_of}</div>}
                    </div>
                  </div>
                );
              })()}

              {/* Corporate Actions */}
              {(upstoxData.corporate_actions?.length > 0) && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Corporate Actions · Upstox</div>
                  <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                    {upstoxData.corporate_actions.slice(0, 12).map((a, i) => {
                      const typeColor = a.type === 'DIVIDEND' ? 'text-cyan'
                        : a.type === 'SPLIT' ? 'text-violet-400'
                        : a.type === 'BONUS' ? 'text-profit'
                        : a.type === 'BUYBACK' ? 'text-amber-400'
                        : 'text-muted';
                      return (
                        <div key={i} className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/[0.02]">
                          <span className={`text-[10px] font-bold uppercase w-16 shrink-0 ${typeColor}`}>{a.type || 'EVENT'}</span>
                          <span className="text-muted text-[11px] w-20 shrink-0 font-mono">{a.ex_date || a.record_date || '—'}</span>
                          <span className="text-slate-300 text-xs flex-1">{a.description || a.purpose || a.remarks}</span>
                          {a.amount && <span className="text-slate-200 text-xs font-mono shrink-0">₹{a.amount}</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Competitor comparison */}
              {upstoxData.competitors?.length > 0 && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Peer Comparison · Upstox</div>
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs min-w-[400px]">
                      <thead>
                        <tr className="border-b border-border bg-surface">
                          <th className="text-left px-3 py-2 text-muted">Company</th>
                          <th className="text-right px-3 py-2 text-muted">PE</th>
                          <th className="text-right px-3 py-2 text-muted">Market Cap</th>
                          <th className="text-right px-3 py-2 text-muted">ROE</th>
                        </tr>
                      </thead>
                      <tbody>
                        {upstoxData.competitors.map((c, i) => (
                          <tr key={i} className="border-b border-border/40 hover:bg-white/[0.02]">
                            <td className="px-3 py-2 text-slate-300 font-medium">{c.name || c.symbol}</td>
                            <td className="px-3 py-2 text-right font-mono text-slate-200">{c.pe_ratio != null ? fmt(c.pe_ratio) : '—'}</td>
                            <td className="px-3 py-2 text-right font-mono text-slate-200">{c.market_cap ? `₹${fmt(c.market_cap/1e7,0)} Cr` : '—'}</td>
                            <td className="px-3 py-2 text-right font-mono text-slate-200">{c.roe != null ? `${fmt(c.roe)}%` : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* News */}
              {upstoxData.news?.length > 0 && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Latest News · Upstox</div>
                  <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                    {upstoxData.news.slice(0, 8).map((n, i) => {
                      const pos = n.sentiment === 'positive';
                      const neg = n.sentiment === 'negative';
                      return (
                        <div key={i} className="flex items-start gap-3 px-4 py-3 hover:bg-white/[0.02]">
                          <span className={`w-1.5 h-1.5 rounded-full mt-2 shrink-0 ${pos ? 'bg-profit' : neg ? 'bg-loss' : 'bg-slate-500'}`} />
                          <div className="flex-1 min-w-0">
                            <div className="text-slate-200 text-xs leading-snug">{n.headline || n.title}</div>
                            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                              {n.source && <span className="text-muted text-[10px]">{n.source}</span>}
                              {n.published_at && <span className="text-muted text-[10px] font-mono">{n.published_at?.slice(0,10)}</span>}
                              {n.url && (
                                <a href={n.url} target="_blank" rel="noopener noreferrer" className="text-cyan text-[10px] hover:underline">Read →</a>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Market Intel (PCR, max pain from Upstox) */}
              {upstoxData.market_intel && (upstoxData.market_intel.pcr != null || upstoxData.market_intel.max_pain != null) && (
                <div>
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-2">Market Intelligence · Upstox</div>
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      ['PCR', upstoxData.market_intel.pcr, upstoxData.market_intel.pcr > 1.2 ? 'text-profit' : upstoxData.market_intel.pcr < 0.8 ? 'text-loss' : 'text-amber-400'],
                      ['Max Pain', upstoxData.market_intel.max_pain ? `₹${upstoxData.market_intel.max_pain?.toLocaleString('en-IN')}` : null, 'text-slate-200'],
                      ['IV', upstoxData.market_intel.iv != null ? `${upstoxData.market_intel.iv}%` : null, upstoxData.market_intel.iv > 40 ? 'text-loss' : 'text-amber-400'],
                    ].filter(([,v]) => v != null).map(([k, v, c]) => (
                      <div key={k} className="bg-surface rounded border border-border/50 p-3 text-center">
                        <div className="text-muted text-[10px] uppercase tracking-wider">{k}</div>
                        <div className={`font-mono text-base font-bold mt-1 ${c}`}>{v}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="text-muted text-[10px] pt-1 border-t border-border">
                Source: Upstox API v2 · Data cached · Zerodha is primary for all trading operations
              </div>
            </div>
          )}

          {!upstoxLoading && !upstoxData && upstoxStatus?.authenticated && (
            <div className="text-muted text-sm py-4">
              No Upstox data returned for {display}. The ISIN may not be in the Upstox instrument list, or the symbol is not yet supported.
            </div>
          )}
        </DeepTab>

        {/* Compare */}
        <DeepTab label="Compare with peers" subtitle="Side-by-side ratio and signal comparison" icon={BarChart2}>
          <div className="space-y-2">
            <p className="text-slate-300 text-sm">Use the search bar (⌘K) → type any NSE symbol → select "Compare" to compare two stocks side by side.</p>
            <p className="text-muted text-xs">Alternatively, open the Sector Peers tab above to see all stocks in the same sector ranked by signal strength.</p>
          </div>
        </DeepTab>
      </section>
      </>}
    </div>
  );
}
