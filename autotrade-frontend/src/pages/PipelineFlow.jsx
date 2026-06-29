import { useState, useEffect } from 'react'
import {
  Activity, Zap, TrendingUp, TrendingDown, BarChart2,
  Database, BrainCircuit, ListFilter, Bot, ShoppingCart,
  Bell, Clock, CheckCircle2, XCircle, AlertCircle,
  RefreshCw, ChevronRight, Wifi, WifiOff,
} from 'lucide-react'
import { apiFetch, getIndiaMarketStatus } from '../api/client'

// ── helpers ──────────────────────────────────────────────────────────────────
function useInterval(fn, ms) {
  useEffect(() => { fn(); const id = setInterval(fn, ms); return () => clearInterval(id) }, [ms])
}

function fmt(n, suffix = '') {
  if (n == null) return '—'
  if (n >= 1e7) return (n / 1e7).toFixed(1) + ' Cr' + suffix
  if (n >= 1e5) return (n / 1e5).toFixed(1) + 'L' + suffix
  return n.toLocaleString('en-IN') + suffix
}

// ── animated connector ────────────────────────────────────────────────────────
function Connector({ active = true, label = '', multi = false }) {
  return (
    <div className="flex flex-col items-center my-1 relative">
      {multi ? (
        <div className="flex items-center gap-16">
          {[0, 1, 2].map(i => (
            <div key={i} className="flex flex-col items-center">
              <div className="relative w-px h-8 bg-border overflow-hidden">
                {active && (
                  <div
                    className="absolute w-full bg-cyan/80 rounded-full"
                    style={{
                      height: 8,
                      animation: `flowDown 1.2s ${i * 0.35}s infinite linear`,
                    }}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center">
          <div className="relative w-px h-8 bg-border overflow-hidden">
            {active && (
              <div
                className="absolute w-full bg-cyan/80 rounded-full"
                style={{ height: 8, animation: 'flowDown 1.2s infinite linear' }}
              />
            )}
          </div>
          {label && <span className="text-[10px] text-muted mt-0.5">{label}</span>}
        </div>
      )}
      <style>{`
        @keyframes flowDown {
          0%   { top: -8px; opacity: 0 }
          20%  { opacity: 1 }
          80%  { opacity: 1 }
          100% { top: 100%; opacity: 0 }
        }
      `}</style>
    </div>
  )
}

// ── merge indicator (3 → 1) ───────────────────────────────────────────────────
function MergeArrow({ active }) {
  return (
    <div className="flex items-center justify-center my-1" style={{ height: 32 }}>
      <svg width="260" height="32" viewBox="0 0 260 32" fill="none">
        {/* left branch */}
        <line x1="50" y1="0" x2="130" y2="32" stroke="#334155" strokeWidth="1.5" />
        {/* center */}
        <line x1="130" y1="0" x2="130" y2="32" stroke="#334155" strokeWidth="1.5" />
        {/* right branch */}
        <line x1="210" y1="0" x2="130" y2="32" stroke="#334155" strokeWidth="1.5" />
        {active && (
          <>
            <circle cx="50"  cy="6"  r="3" fill="#22D3EE" opacity="0.85">
              <animateMotion dur="0.9s" repeatCount="indefinite" begin="0s"
                path="M0,0 L80,26" />
            </circle>
            <circle cx="130" cy="4"  r="3" fill="#22D3EE" opacity="0.85">
              <animateMotion dur="0.9s" repeatCount="indefinite" begin="0.3s"
                path="M0,0 L0,28" />
            </circle>
            <circle cx="210" cy="6"  r="3" fill="#22D3EE" opacity="0.85">
              <animateMotion dur="0.9s" repeatCount="indefinite" begin="0.6s"
                path="M0,0 L-80,26" />
            </circle>
          </>
        )}
      </svg>
    </div>
  )
}

// ── status pill ───────────────────────────────────────────────────────────────
function StatusPill({ ok, label }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full
      ${ok ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`} />
      {label}
    </span>
  )
}

// ── single pipeline node ──────────────────────────────────────────────────────
function Node({ Icon, title, subtitle, timing, stats = [], accent = 'cyan', status, wide = false, children }) {
  const borderColor = {
    cyan:    'border-cyan/30',
    emerald: 'border-emerald-500/30',
    blue:    'border-blue-500/30',
    amber:   'border-amber-500/30',
    purple:  'border-purple-500/30',
    rose:    'border-rose-500/30',
  }[accent] ?? 'border-border'

  const bgGlow = {
    cyan:    'rgba(6,182,212,0.04)',
    emerald: 'rgba(16,185,129,0.04)',
    blue:    'rgba(59,130,246,0.04)',
    amber:   'rgba(245,158,11,0.04)',
    purple:  'rgba(168,85,247,0.04)',
    rose:    'rgba(244,63,94,0.04)',
  }[accent] ?? 'transparent'

  const iconColor = {
    cyan:    'text-cyan',
    emerald: 'text-emerald-400',
    blue:    'text-blue-400',
    amber:   'text-amber-400',
    purple:  'text-purple-400',
    rose:    'text-rose-400',
  }[accent] ?? 'text-slate-300'

  return (
    <div
      className={`rounded-2xl border ${borderColor} p-4 ${wide ? 'w-full max-w-2xl' : 'w-64'}`}
      style={{ background: bgGlow }}
    >
      <div className="flex items-start gap-3">
        <div className={`p-2 rounded-xl border ${borderColor} shrink-0`} style={{ background: bgGlow }}>
          <Icon size={18} className={iconColor} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-100 font-semibold text-sm">{title}</span>
            {timing && (
              <span className="flex items-center gap-1 text-[10px] text-muted">
                <Clock size={10} /> {timing}
              </span>
            )}
          </div>
          {subtitle && <p className="text-muted text-xs mt-0.5">{subtitle}</p>}
          {status != null && (
            <div className="mt-1">
              <StatusPill ok={status} label={status ? 'ACTIVE' : 'IDLE'} />
            </div>
          )}
        </div>
      </div>

      {stats.length > 0 && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          {stats.map(s => (
            <div key={s.label} className="rounded-lg border border-border/50 px-2.5 py-1.5"
              style={{ background: 'rgba(255,255,255,0.02)' }}>
              <p className="text-[10px] text-muted uppercase tracking-wide">{s.label}</p>
              <p className={`font-bold text-sm tabular-nums ${s.color ?? 'text-slate-200'}`}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      {children}
    </div>
  )
}

// ── 3-column row for parallel tasks ──────────────────────────────────────────
function SmallNode({ Icon, title, subtitle, accent = 'cyan', timing, badge }) {
  const borderColor = {
    cyan:    'border-cyan/25',
    emerald: 'border-emerald-500/25',
    blue:    'border-blue-400/25',
  }[accent]

  const iconColor = {
    cyan:    'text-cyan',
    emerald: 'text-emerald-400',
    blue:    'text-blue-400',
  }[accent]

  return (
    <div className={`flex-1 rounded-xl border ${borderColor} p-3`}
      style={{ background: 'rgba(255,255,255,0.025)', minWidth: 0 }}>
      <div className="flex items-center gap-2 mb-1">
        <Icon size={14} className={iconColor} />
        <span className="text-xs font-semibold text-slate-200 truncate">{title}</span>
        {badge && (
          <span className="ml-auto text-[10px] font-bold bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 rounded-full shrink-0">
            {badge}
          </span>
        )}
      </div>
      <p className="text-[11px] text-muted leading-snug">{subtitle}</p>
      {timing && (
        <p className="text-[10px] text-muted/70 mt-1 flex items-center gap-1">
          <Clock size={9} /> {timing}
        </p>
      )}
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────
export default function PipelineFlow() {
  const [market, setMarket]     = useState(null)
  const [agent,  setAgent]      = useState(null)
  const [intel,  setIntel]      = useState(null)
  const [scores, setScores]     = useState(null)
  const [loading, setLoading]   = useState(true)

  const load = async () => {
    try {
      const [mkt, ag, ctx, sc] = await Promise.allSettled([
        getIndiaMarketStatus(),
        apiFetch('/api/v1/agent/status'),
        apiFetch('/api/v1/intelligence/context'),
        apiFetch('/api/v1/intelligence/scores?limit=200'),
      ])
      if (mkt.status === 'fulfilled')  setMarket(mkt.value)
      if (ag.status  === 'fulfilled')  setAgent(ag.value)
      if (ctx.status === 'fulfilled')  setIntel(ctx.value)
      if (sc.status  === 'fulfilled')  setScores(sc.value)
    } finally {
      setLoading(false)
    }
  }

  useInterval(load, 30_000)

  const isOpen      = market?.nse_open ?? false
  const nifty       = market?.nifty
  const portfolio   = agent?.portfolio
  const shortlistN  = Array.isArray(scores) ? scores.length : 0
  const buyN        = Array.isArray(scores) ? scores.filter(s => s.signal === 'BUY' || s.signal === 'STRONG_BUY').length : 0

  const niftyPct    = nifty?.change_pct ?? 0
  const niftyColor  = niftyPct >= 0 ? 'text-emerald-400' : 'text-red-400'

  return (
    <div className="max-w-3xl mx-auto space-y-2 pb-12">
      {/* header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Pipeline Flow</h1>
          <p className="text-muted text-sm mt-0.5">Live view of how Prajna finds and trades stocks</p>
        </div>
        <div className="flex items-center gap-3">
          {loading && <RefreshCw size={14} className="text-muted animate-spin" />}
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-semibold
            ${isOpen
              ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
              : 'border-slate-600 text-slate-400 bg-slate-800/40'}`}>
            {isOpen ? <Wifi size={14} /> : <WifiOff size={14} />}
            NSE {isOpen ? 'OPEN' : 'CLOSED'}
          </div>
          {nifty && (
            <div className={`text-sm font-bold tabular-nums ${niftyColor}`}>
              Nifty {nifty.price?.toLocaleString('en-IN')}
              <span className="text-xs ml-1">({niftyPct >= 0 ? '+' : ''}{niftyPct?.toFixed(2)}%)</span>
            </div>
          )}
        </div>
      </div>

      {/* ── STEP 1: Market Open trigger ─────────────────────────────────────── */}
      <div className="flex justify-center">
        <div className={`flex items-center gap-3 px-5 py-3 rounded-2xl border font-semibold text-sm
          ${isOpen
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
            : 'border-border bg-white/[0.02] text-slate-400'}`}>
          <span className={`w-2.5 h-2.5 rounded-full ${isOpen ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}`} />
          NSE Market {isOpen ? 'OPEN — 9:15 AM to 3:30 PM IST' : 'CLOSED — Opens 9:15 AM IST tomorrow'}
        </div>
      </div>

      <Connector active={isOpen} />

      {/* ── STEP 2: Three parallel sources ──────────────────────────────────── */}
      <div className="rounded-2xl border border-border p-4" style={{ background: 'rgba(255,255,255,0.015)' }}>
        <p className="text-[11px] text-muted uppercase tracking-widest mb-3 flex items-center gap-1.5">
          <Activity size={11} /> Parallel Data Sources — Every 5 min
        </p>
        <div className="flex gap-3">
          <SmallNode
            Icon={TrendingUp}
            accent="cyan"
            title="Price Crawler"
            subtitle="Fetches live candles via Zerodha Kite + yfinance for all watchlist & hub symbols"
            timing="every 5 min"
          />
          <SmallNode
            Icon={Zap}
            accent="emerald"
            title="Breakout Screener"
            subtitle="Scans all ~3,300 NSE stocks for price ≥4% + vol ≥2× + RSI <85 + close >EMA20"
            timing="every 5 min"
            badge="NEW"
          />
          <SmallNode
            Icon={BarChart2}
            accent="blue"
            title="Market Breadth"
            subtitle="NSE advances / declines, FII/DII flow, VIX, 52W highs → macro bias score"
            timing="every 5 min"
          />
        </div>
      </div>

      {/* breakout detail */}
      <div className="ml-4 pl-4 border-l border-emerald-500/20 text-xs text-muted space-y-1">
        <p className="flex items-center gap-1.5">
          <CheckCircle2 size={11} className="text-emerald-400 shrink-0" />
          Breakout stocks → injected into <span className="text-slate-300 font-medium">hub_universe</span> + <span className="text-slate-300 font-medium">user_watchlist</span>
        </p>
        <p className="flex items-center gap-1.5">
          <Bell size={11} className="text-amber-400 shrink-0" />
          Telegram alert fires with the breakout list
        </p>
        <p className="flex items-center gap-1.5">
          <AlertCircle size={11} className="text-slate-500 shrink-0" />
          Gate: only runs when NSE is open
        </p>
      </div>

      <MergeArrow active={isOpen} />

      {/* ── STEP 3: Hub Universe ─────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Database}
          title="Hub Universe"
          subtitle="All NSE stocks eligible for 7-factor scoring"
          timing="rebuilt daily 7:00 AM"
          accent="blue"
          wide
          stats={[
            { label: 'Total stocks', value: '1,218+', color: 'text-cyan' },
            { label: 'Threshold', value: '≥ ₹5 Cr/day', color: 'text-slate-200' },
            { label: 'Breakout inject', value: 'up to +20', color: 'text-emerald-400' },
            { label: 'Rebuilt', value: 'daily + live', color: 'text-slate-200' },
          ]}
        >
          <div className="mt-3 text-[11px] text-muted space-y-1 border-t border-border pt-3">
            <p className="flex gap-2">
              <span className="text-slate-400 font-medium w-24 shrink-0">Large-cap</span>
              <span>₹20 Cr+ turnover/day — always included (legacy top-500)</span>
            </p>
            <p className="flex gap-2">
              <span className="text-slate-400 font-medium w-24 shrink-0">Mid-cap</span>
              <span>₹5–20 Cr/day — <span className="text-emerald-400">459 stocks added</span> after threshold cut</span>
            </p>
            <p className="flex gap-2">
              <span className="text-slate-400 font-medium w-24 shrink-0">Breakouts</span>
              <span>Any stock that moves 4%+ on heavy vol — <span className="text-emerald-400">injected live</span></span>
            </p>
          </div>
        </Node>
      </div>

      <Connector active label="every 15 min" />

      {/* ── STEP 4: 7-Factor Scorer ──────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={BrainCircuit}
          title="7-Factor Hub Scorer"
          subtitle="Computes a conviction score for each stock in the universe"
          timing="every 15 min"
          accent="purple"
          wide
        >
          <div className="mt-3 grid grid-cols-1 gap-1.5">
            {[
              { factor: 'Technical',   weight: '35%', color: 'bg-cyan',          desc: 'RSI, MACD, Bollinger Bands, EMA, volume trend' },
              { factor: 'Sector',      weight: '15%', color: 'bg-blue-500',      desc: 'Sector momentum vs Nifty benchmark' },
              { factor: 'News',        weight: '15%', color: 'bg-amber-500',     desc: 'LLM sentiment on recent headlines (Ollama)' },
              { factor: 'Macro',       weight: '10%', color: 'bg-emerald-500',   desc: 'Market breadth, VIX, FII/DII flow' },
              { factor: 'Earnings',    weight: '10%', color: 'bg-orange-500',    desc: 'EPS surprise, guidance, upcoming dates' },
              { factor: 'Fundamental', weight: '10%', color: 'bg-indigo-500',    desc: 'P/E, ROE, debt ratio, promoter holding' },
              { factor: 'Options',     weight: '5%',  color: 'bg-purple-500',    desc: 'PCR, IV skew, OI buildup (F&O enabled)' },
            ].map(f => (
              <div key={f.factor} className="flex items-center gap-3 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${f.color}`} />
                <span className="text-slate-300 font-medium w-24 shrink-0">{f.factor}</span>
                <span className="text-cyan/80 font-bold w-8 shrink-0">{f.weight}</span>
                <span className="text-muted">{f.desc}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-border flex items-center gap-4 text-xs">
            <span className="text-muted">Score range:</span>
            <span className="text-red-400 font-bold">−100</span>
            <div className="flex-1 h-1.5 rounded-full bg-gradient-to-r from-red-500 via-slate-600 to-emerald-500" />
            <span className="text-emerald-400 font-bold">+200</span>
          </div>
        </Node>
      </div>

      <Connector active label="top 100 by score" />

      {/* ── STEP 5: Market Shortlist ──────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={ListFilter}
          title="Market Shortlist"
          subtitle="Top-100 BUY/STRONG_BUY stocks — the only universe the agent reads"
          timing="rebuilt every 15 min"
          accent="amber"
          wide
          stats={[
            { label: 'Scored stocks', value: shortlistN > 0 ? shortlistN : '—', color: 'text-amber-300' },
            { label: 'BUY signals',   value: buyN > 0 ? buyN : '—',             color: 'text-emerald-400' },
            { label: 'Hub gate',      value: 'score ≥ 50',                       color: 'text-slate-200' },
            { label: 'Signal filter', value: 'BUY / STRONG_BUY',                  color: 'text-slate-200' },
          ]}
        />
      </div>

      <Connector active label="every 5 min" />

      {/* ── STEP 6: Agent Trade Loop ──────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Bot}
          title="India Trade Loop"
          subtitle="Reads shortlist, applies LLM reasoning, checks risk gates, decides"
          timing="every 5 min (market hours)"
          accent="emerald"
          status={agent?.enabled && isOpen}
          wide
          stats={[
            { label: 'Mode',          value: agent?.paper_mode ? 'PAPER' : 'LIVE', color: agent?.paper_mode ? 'text-blue-400' : 'text-emerald-400' },
            { label: 'Open positions',value: portfolio?.open_positions_count ?? '—', color: 'text-slate-200' },
            { label: 'Cash',          value: portfolio?.cash ? `₹${(portfolio.cash/100000).toFixed(1)}L` : '—', color: 'text-slate-200' },
            { label: 'Decisions today',value: agent?.decisions_today ?? 0, color: 'text-slate-200' },
          ]}
        >
          <div className="mt-3 pt-3 border-t border-border text-xs text-muted space-y-1">
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400" />
              Reads top-120 from shortlist (BUY / STRONG_BUY / HOLD)
            </p>
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400" />
              Portfolio-level cognitive cycle — macro check, sector rotation, portfolio heat
            </p>
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400" />
              Per-symbol LLM gate via <span className="text-slate-300 font-medium">qwen2.5:7b</span> (local Ollama) — reads chart brief + news
            </p>
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400" />
              Capital sizing: 80% of ₹20L wallet spread across open positions
            </p>
          </div>
        </Node>
      </div>

      {/* ── STEP 7: Decision fork ─────────────────────────────────────────────── */}
      <div className="flex justify-center gap-8 mt-2">
        {/* buy branch */}
        <div className="flex flex-col items-center gap-2">
          <div className="relative w-px h-6 bg-border overflow-hidden">
            {isOpen && <div className="absolute w-full bg-emerald-400/80 rounded-full"
              style={{ height: 8, animation: 'flowDown 1s infinite linear' }} />}
          </div>
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/8 px-4 py-2 flex items-center gap-2">
            <ShoppingCart size={14} className="text-emerald-400" />
            <span className="text-sm font-semibold text-emerald-300">BUY Order</span>
          </div>
          <div className="text-[10px] text-muted text-center max-w-[140px]">
            Placed on Zerodha Kite<br />(or Paper DB in paper mode)
          </div>
        </div>

        {/* skip branch */}
        <div className="flex flex-col items-center gap-2 opacity-50">
          <div className="w-px h-6 bg-border" />
          <div className="rounded-xl border border-border px-4 py-2 flex items-center gap-2">
            <XCircle size={14} className="text-slate-500" />
            <span className="text-sm font-medium text-slate-500">SKIP</span>
          </div>
          <div className="text-[10px] text-muted text-center max-w-[140px]">
            Low conviction or<br />risk gate triggered
          </div>
        </div>
      </div>

      {/* merge back to telegram */}
      <div className="flex justify-center mt-2">
        <div className="relative w-px h-6 bg-border overflow-hidden">
          {isOpen && <div className="absolute w-full bg-amber-400/80 rounded-full"
            style={{ height: 8, animation: 'flowDown 1s infinite linear' }} />}
        </div>
      </div>

      {/* ── STEP 8: Telegram ─────────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <div className="rounded-2xl border border-amber-500/25 bg-amber-500/5 px-6 py-4 flex items-center gap-4 w-full max-w-2xl">
          <div className="p-2.5 rounded-xl border border-amber-500/25 bg-amber-500/10">
            <Bell size={18} className="text-amber-400" />
          </div>
          <div>
            <p className="text-sm font-semibold text-amber-300">Telegram Alert</p>
            <p className="text-xs text-muted mt-0.5">
              Every BUY order + every breakout discovery → instant Telegram message with symbol, price, qty, hub score, and reasoning
            </p>
          </div>
        </div>
      </div>

      {/* ── timing summary ───────────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-border p-5 mt-4 glass-panel">
        <p className="text-xs font-semibold text-slate-300 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Clock size={12} className="text-cyan" /> What happens tomorrow when market opens
        </p>
        <div className="space-y-2">
          {[
            { time: '9:15 AM', event: 'NSE market opens', color: 'text-emerald-400', dot: 'bg-emerald-400' },
            { time: '9:21 AM', event: 'First breakout scan fires (5-min schedule + 60s offset)', color: 'text-cyan', dot: 'bg-cyan' },
            { time: '9:21 AM', event: 'Breakout stocks injected → hub_universe + user_watchlist', color: 'text-cyan', dot: 'bg-cyan' },
            { time: '9:21 AM', event: 'Telegram: "Breakout Auto-Discovery" alert sent', color: 'text-amber-400', dot: 'bg-amber-400' },
            { time: '9:30 AM', event: 'Hub scorer runs → 7-factor scores for all 1,218+ symbols', color: 'text-purple-400', dot: 'bg-purple-400' },
            { time: '9:30 AM', event: 'market_shortlist rebuilt with top-100', color: 'text-amber-300', dot: 'bg-amber-400' },
            { time: '9:35 AM', event: 'Agent loop reads shortlist → LLM reasoning → orders placed', color: 'text-emerald-300', dot: 'bg-emerald-400' },
            { time: 'Repeat',  event: 'Breakout scan every 5 min | Hub + shortlist every 15 min', color: 'text-slate-400', dot: 'bg-slate-500' },
          ].map((row, i) => (
            <div key={i} className="flex items-start gap-3">
              <span className="text-[11px] font-mono text-muted w-16 shrink-0 pt-0.5">{row.time}</span>
              <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${row.dot}`} />
              <span className={`text-xs ${row.color}`}>{row.event}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── ROTO fix callout ─────────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-cyan/20 p-4 flex gap-4" style={{ background: 'rgba(6,182,212,0.04)' }}>
        <Zap size={20} className="text-cyan shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-semibold text-cyan mb-1">The ROTO Fix</p>
          <div className="text-xs text-muted space-y-1">
            <p><span className="text-red-400 font-medium">Before:</span> ROTO avg turnover ₹10 Cr &lt; ₹20 Cr threshold → invisible to agent</p>
            <p><span className="text-emerald-400 font-medium">After:</span> Threshold lowered to ₹5 Cr → ROTO now at hub rank #972 (of 1,218)</p>
            <p><span className="text-emerald-400 font-medium">Also:</span> Breakout screener catches any sudden 4%+ move within 5 min of it happening</p>
          </div>
        </div>
      </div>
    </div>
  )
}
