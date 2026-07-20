import { useState, useEffect } from 'react'
import {
  Activity, Zap, TrendingUp, BarChart2,
  Database, BrainCircuit, ListFilter, Bot, ShoppingCart,
  Bell, Clock, CheckCircle2, XCircle, AlertCircle,
  RefreshCw, ChevronRight, Wifi, WifiOff, Newspaper,
  Gauge, ShieldCheck, FileSearch,
} from 'lucide-react'
import { apiFetch, getIndiaMarketStatus } from '../api/client'

// ── helpers ──────────────────────────────────────────────────────────────────
function useInterval(fn, ms) {
  useEffect(() => { fn(); const id = setInterval(fn, ms); return () => clearInterval(id) }, [ms])
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

// ── merge indicator (N → 1) ───────────────────────────────────────────────────
function MergeArrow({ active }) {
  return (
    <div className="flex items-center justify-center my-1" style={{ height: 32 }}>
      <svg width="260" height="32" viewBox="0 0 260 32" fill="none">
        <line x1="50" y1="0" x2="130" y2="32" stroke="#334155" strokeWidth="1.5" />
        <line x1="130" y1="0" x2="130" y2="32" stroke="#334155" strokeWidth="1.5" />
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

// ── branch indicator (1 → N) ─────────────────────────────────────────────────
function BranchArrow({ active }) {
  return (
    <div className="flex items-center justify-center my-1" style={{ height: 32 }}>
      <svg width="520" height="32" viewBox="0 0 520 32" fill="none">
        <line x1="260" y1="0" x2="80"  y2="32" stroke="#334155" strokeWidth="1.5" />
        <line x1="260" y1="0" x2="260" y2="32" stroke="#334155" strokeWidth="1.5" />
        <line x1="260" y1="0" x2="440" y2="32" stroke="#334155" strokeWidth="1.5" />
        {active && (
          <>
            <circle r="3" fill="#22D3EE" opacity="0.85">
              <animateMotion dur="1s" repeatCount="indefinite" begin="0s" path="M260,2 L80,30" />
            </circle>
            <circle r="3" fill="#22D3EE" opacity="0.85">
              <animateMotion dur="1s" repeatCount="indefinite" begin="0.33s" path="M260,2 L260,30" />
            </circle>
            <circle r="3" fill="#22D3EE" opacity="0.85">
              <animateMotion dur="1s" repeatCount="indefinite" begin="0.66s" path="M260,2 L440,30" />
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

// ── small card for parallel tasks (auto-wrapping grid) ────────────────────────
function SmallNode({ Icon, title, subtitle, accent = 'cyan', timing, badge }) {
  const borderColor = {
    cyan:    'border-cyan/25',
    emerald: 'border-emerald-500/25',
    blue:    'border-blue-400/25',
    amber:   'border-amber-500/25',
    purple:  'border-purple-500/25',
  }[accent]

  const iconColor = {
    cyan:    'text-cyan',
    emerald: 'text-emerald-400',
    blue:    'text-blue-400',
    amber:   'text-amber-400',
    purple:  'text-purple-400',
  }[accent]

  return (
    <div className={`rounded-xl border ${borderColor} p-3`}
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

// ── decision-path card (Path A / B / C) ────────────────────────────────────────
function PathCard({ letter, title, Icon, accent, active, timing, points }) {
  const cfg = {
    emerald: { border: 'border-emerald-500/30', bg: 'rgba(16,185,129,0.04)', icon: 'text-emerald-400', badge: 'bg-emerald-500/15 text-emerald-400' },
    blue:    { border: 'border-blue-500/30',    bg: 'rgba(59,130,246,0.04)', icon: 'text-blue-400',    badge: 'bg-blue-500/15 text-blue-400' },
    amber:   { border: 'border-amber-500/30',   bg: 'rgba(245,158,11,0.04)', icon: 'text-amber-400',   badge: 'bg-amber-500/15 text-amber-400' },
  }[accent]
  return (
    <div className={`flex-1 min-w-0 rounded-2xl border ${cfg.border} p-4`} style={{ background: cfg.bg }}>
      <div className="flex items-center gap-2 mb-1.5 min-w-0">
        <span className={`text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center border ${cfg.border} shrink-0 ${cfg.icon}`}>{letter}</span>
        <Icon size={15} className={`${cfg.icon} shrink-0`} />
        <span className="text-sm font-semibold text-slate-100 break-words min-w-0">{title}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-2">
        <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap ${active ? cfg.badge : 'bg-slate-500/15 text-slate-400'}`}>
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${active ? `${cfg.icon.replace('text-', 'bg-')} animate-pulse` : 'bg-slate-500'}`} />
          {active ? 'WIRED TO EXECUTION' : 'DECISION-ONLY — NOT WIRED'}
        </span>
        <span className="flex items-center gap-1 text-[10px] text-muted"><Clock size={9} className="shrink-0" /> {timing}</span>
      </div>
      <div className="space-y-1">
        {points.map((p, i) => (
          <p key={i} className="text-[11px] text-muted leading-snug flex items-start gap-1.5 min-w-0">
            <ChevronRight size={10} className={`${cfg.icon} mt-0.5 shrink-0`} />
            <span className="min-w-0 break-words">{p}</span>
          </p>
        ))}
      </div>
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────
export default function PipelineFlow() {
  const [market, setMarket]     = useState(null)
  const [agent,  setAgent]      = useState(null)
  const [scores, setScores]     = useState(null)
  const [loading, setLoading]   = useState(true)

  const load = async () => {
    try {
      const [mkt, ag, sc] = await Promise.allSettled([
        getIndiaMarketStatus(),
        apiFetch('/api/v1/agent/status'),
        apiFetch('/api/v1/intelligence/scores?limit=200'),
      ])
      if (mkt.status === 'fulfilled')  setMarket(mkt.value)
      if (ag.status  === 'fulfilled')  setAgent(ag.value)
      if (sc.status  === 'fulfilled')  setScores(sc.value)
    } finally {
      setLoading(false)
    }
  }

  useInterval(load, 30_000)

  const isOpen      = market?.nse_open ?? false
  const nifty       = market?.nifty
  const portfolio   = agent?.portfolio
  const scoredN     = Array.isArray(scores) ? scores.length : 0
  const buyN        = Array.isArray(scores) ? scores.filter(s => s.signal === 'BUY' || s.signal === 'STRONG_BUY').length : 0

  const niftyPct    = nifty?.change_pct ?? 0
  const niftyColor  = niftyPct >= 0 ? 'text-emerald-400' : 'text-red-400'

  return (
    <div className="max-w-3xl mx-auto space-y-2 pb-12">
      {/* header */}
      <div className="flex items-center justify-between flex-wrap gap-y-3 gap-x-4 mb-6">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-slate-100">Pipeline Flow</h1>
          <p className="text-muted text-sm mt-0.5">Live, source-verified view of how Prajna discovers, scores, and trades stocks</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {loading && <RefreshCw size={14} className="text-muted animate-spin shrink-0" />}
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-semibold whitespace-nowrap shrink-0
            ${isOpen
              ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
              : 'border-slate-600 text-slate-400 bg-slate-800/40'}`}>
            {isOpen ? <Wifi size={14} /> : <WifiOff size={14} />}
            NSE {isOpen ? 'OPEN' : 'CLOSED'}
          </div>
          {nifty && (
            <div className={`text-sm font-bold tabular-nums whitespace-nowrap shrink-0 ${niftyColor}`}>
              Nifty {nifty.price?.toLocaleString('en-IN')}
              <span className="text-xs ml-1">({niftyPct >= 0 ? '+' : ''}{niftyPct?.toFixed(2)}%)</span>
            </div>
          )}
        </div>
      </div>

      {/* ── STEP 1: Market Open trigger ─────────────────────────────────────── */}
      <div className="flex justify-center px-2">
        <div className={`flex items-center gap-3 px-5 py-3 rounded-2xl border font-semibold text-sm text-center sm:text-left max-w-full
          ${isOpen
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
            : 'border-border bg-white/[0.02] text-slate-400'}`}>
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${isOpen ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}`} />
          <span className="break-words">NSE Market {isOpen ? 'OPEN — 9:15 AM to 3:30 PM IST' : 'CLOSED — Opens 9:15 AM IST next session'}</span>
        </div>
      </div>

      <Connector active={isOpen} />

      {/* ── STEP 2: Ingestion & discovery ───────────────────────────────────── */}
      <div className="rounded-2xl border border-border p-4" style={{ background: 'rgba(255,255,255,0.015)' }}>
        <p className="text-[11px] text-muted uppercase tracking-widest mb-3 flex items-center gap-1.5">
          <Activity size={11} /> Continuous Ingestion &amp; Discovery — celery beat, real schedules
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <SmallNode
            Icon={TrendingUp}
            accent="cyan"
            title="Price Scan"
            subtitle="OHLCV candles + NIFTY/SENSEX/BANKNIFTY/VIX snapshots via Zerodha Kite + yfinance backstop"
            timing="every 30s"
          />
          <SmallNode
            Icon={Zap}
            accent="emerald"
            title="Breakout Screener"
            subtitle="Scans all NSE symbols: price ≥4% + vol ≥2x + RSI <85 + close >EMA20. Gated: only when NSE is open."
            timing="every 5 min (+60s offset)"
          />
          <SmallNode
            Icon={TrendingUp}
            accent="purple"
            title="Momentum Discovery"
            subtitle="Catches slow 30-day grinders breakout misses (10-100% over 30d, e.g. SAKSOFT +55%, JTEKTINDIA +16%). Runs any time of day — not market-hours gated."
            timing="every 30 min"
            badge="NEW"
          />
          <SmallNode
            Icon={BarChart2}
            accent="blue"
            title="Narrative / Macro Intel"
            subtitle="FII/DII flow, VIX, sector rotation, market-wide news score → MasterContext cache read by the scorer"
            timing="every 5 min"
          />
          <SmallNode
            Icon={Newspaper}
            accent="amber"
            title="Event Discovery & Clustering"
            subtitle="Scrapes RSS/APIs, runs semantic clustering to prevent duplication, extracts category, half-life, and structured entities via LLM."
            timing="every 15s"
            badge="V4 ENGINE"
          />
          <SmallNode
            Icon={Gauge}
            accent="cyan"
            title="Options Chain Refresh"
            subtitle="NIFTY/BANKNIFTY/FINNIFTY chain, Greeks, PCR, max-pain, IV-rank — feeds the F&O pipeline"
            timing="every 15 min"
          />
        </div>
      </div>

      {/* discovery detail */}
      <div className="ml-4 pl-4 border-l border-emerald-500/20 text-xs text-muted space-y-1">
        <p className="flex items-center gap-1.5">
          <CheckCircle2 size={11} className="text-emerald-400 shrink-0" />
          Breakout + momentum hits → injected into <span className="text-slate-300 font-medium">hub_universe</span> + <span className="text-slate-300 font-medium">user_watchlist</span>
        </p>
        <p className="flex items-center gap-1.5">
          <Bell size={11} className="text-amber-400 shrink-0" />
          Telegram alert fires with the breakout / momentum list
        </p>
        <p className="flex items-center gap-1.5">
          <AlertCircle size={11} className="text-slate-500 shrink-0" />
          Outside market hours, news-engine candidates queue in <span className="text-slate-300 font-medium">PreMarketNewsQueue</span> and drain at next open
        </p>
      </div>

      <MergeArrow active={isOpen} />

      {/* ── STEP 3: Hub Universe ─────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Database}
          title="Hub Universe"
          subtitle="All NSE equities eligible for scoring, ranked by 30-day avg daily turnover"
          timing="rebuilt daily 9:00 AM IST + live injection"
          accent="blue"
          wide
          stats={[
            { label: 'Turnover floor', value: '≥ ₹1 Cr/day', color: 'text-cyan' },
            { label: 'Universe cap', value: 'top ~3,000', color: 'text-slate-200' },
            { label: 'Live inject', value: 'breakout + momentum', color: 'text-emerald-400' },
            { label: 'Rebuild', value: 'daily 03:30 UTC', color: 'text-slate-200' },
          ]}
        >
          <div className="mt-3 text-[11px] text-muted space-y-1 border-t border-border pt-3">
            <p>
              Single-tier rank by <code className="text-cyan bg-slate-800 px-1 rounded">AVG(volume × close)</code> over the
              last 30 sessions, falling back to 1h-candle aggregation if daily candles are thin.
            </p>
            <p>
              Threshold has been progressively lowered from ₹20 Cr → ₹5 Cr → <span className="text-emerald-400 font-medium">₹1 Cr/day</span> so
              small-caps that move on real volume (e.g. JTEKTINDIA ~₹4 Cr, SAKSOFT ~₹4.5 Cr, SIGNPOST ~₹3 Cr) are never invisible to the scorer.
            </p>
          </div>
        </Node>
      </div>

      <Connector active label="rescored ~every 15 min" />

      {/* ── STEP 4: Master Intelligence Scorer (V4 Multi-Strategy) ───────────── */}
      <div className="flex justify-center">
        <Node
          Icon={BrainCircuit}
          title="Master Intelligence Scorer (V4 Multi-Strategy)"
          subtitle="engine/intelligence_hub.py — dynamic strategy gating based on market regime and signal strength"
          timing="every 15 min (:14/:29/:44/:59 marks)"
          accent="purple"
          wide
        >
          <div className="mt-3 space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <p className="text-[10px] uppercase tracking-wider text-emerald-400 mb-1 font-bold flex items-center gap-1"><Zap size={10} /> Event Swing Strategy</p>
                <div className="grid grid-cols-1 gap-1">
                  {[
                    { factor: 'News/Catalyst', weight: '40%', color: 'bg-amber-500' },
                    { factor: 'Technical', weight: '30%', color: 'bg-cyan' },
                    { factor: 'Sector', weight: '10%', color: 'bg-emerald-500' },
                    { factor: 'Macro', weight: '10%', color: 'bg-indigo-500' },
                    { factor: 'Volume', weight: '10%', color: 'bg-blue-500' },
                  ].map(f => (
                    <div key={f.factor} className="flex items-center gap-3 text-[11px]">
                      <span className={`w-1 h-1 rounded-full shrink-0 ${f.color}`} />
                      <span className="text-slate-300 font-medium flex-1">{f.factor}</span>
                      <span className="text-cyan/80 font-bold w-8 text-right shrink-0">{f.weight}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-wider text-cyan mb-1 font-bold flex items-center gap-1"><TrendingUp size={10} /> Technical Swing Strategy</p>
                <div className="grid grid-cols-1 gap-1">
                  {[
                    { factor: 'Technical', weight: '45%', color: 'bg-cyan' },
                    { factor: 'News', weight: '20%', color: 'bg-amber-500' },
                    { factor: 'Volume', weight: '15%', color: 'bg-blue-500' },
                    { factor: 'Sector', weight: '10%', color: 'bg-emerald-500' },
                    { factor: 'Macro', weight: '10%', color: 'bg-indigo-500' },
                  ].map(f => (
                    <div key={f.factor} className="flex items-center gap-3 text-[11px]">
                      <span className={`w-1 h-1 rounded-full shrink-0 ${f.color}`} />
                      <span className="text-slate-300 font-medium flex-1">{f.factor}</span>
                      <span className="text-cyan/80 font-bold w-8 text-right shrink-0">{f.weight}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-wider text-blue-400 mb-1 font-bold flex items-center gap-1"><Activity size={10} /> Intraday Momentum</p>
                <div className="grid grid-cols-1 gap-1">
                  {[
                    { factor: 'Technical', weight: '50%', color: 'bg-cyan' },
                    { factor: 'Volume', weight: '25%', color: 'bg-blue-500' },
                    { factor: 'Options (PCR/IV)', weight: '15%', color: 'bg-purple-500' },
                    { factor: 'News', weight: '5%', color: 'bg-amber-500' },
                    { factor: 'Macro', weight: '5%', color: 'bg-indigo-500' },
                  ].map(f => (
                    <div key={f.factor} className="flex items-center gap-3 text-[11px]">
                      <span className={`w-1 h-1 rounded-full shrink-0 ${f.color}`} />
                      <span className="text-slate-300 font-medium flex-1">{f.factor}</span>
                      <span className="text-cyan/80 font-bold w-8 text-right shrink-0">{f.weight}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-300 mb-1 font-bold flex items-center gap-1"><Database size={10} /> Positional Investment</p>
                <div className="grid grid-cols-1 gap-1">
                  {[
                    { factor: 'Fundamentals', weight: '40%', color: 'bg-slate-400' },
                    { factor: 'Earnings', weight: '20%', color: 'bg-amber-200' },
                    { factor: 'Technical', weight: '20%', color: 'bg-cyan' },
                    { factor: 'Macro', weight: '10%', color: 'bg-indigo-500' },
                    { factor: 'Sector', weight: '10%', color: 'bg-emerald-500' },
                  ].map(f => (
                    <div key={f.factor} className="flex items-center gap-3 text-[11px]">
                      <span className={`w-1 h-1 rounded-full shrink-0 ${f.color}`} />
                      <span className="text-slate-300 font-medium flex-1">{f.factor}</span>
                      <span className="text-cyan/80 font-bold w-8 text-right shrink-0">{f.weight}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
          <p className="mt-3 text-[10px] text-muted italic border-t border-border pt-2 flex gap-1 items-start">
            <ShieldCheck size={12} className="text-amber-500 shrink-0 mt-0.5" />
            <span>
              <strong>Gating Logic:</strong> Engine automatically picks Event Swing if News ≥ 85 & Tech ≥ 60. 
              Picks Technical Swing if Tech ≥ 85 & Vol ≥ 70. 
              A flat −20 penalty applies across all swing strategies when Nifty macro regime is BEAR.
            </span>
          </p>
          <div className="mt-3 pt-3 border-t border-border grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
            <div className="rounded-lg border border-emerald-500/20 px-2 py-1.5 bg-emerald-500/5">
              <p className="text-emerald-400 font-bold">STRONG_BUY</p><p className="text-muted">≥ 60 (≥40 swing)</p>
            </div>
            <div className="rounded-lg border border-cyan/20 px-2 py-1.5 bg-cyan/5">
              <p className="text-cyan font-bold">BUY</p><p className="text-muted">≥ 25</p>
            </div>
            <div className="rounded-lg border border-slate-600 px-2 py-1.5 bg-slate-800/20">
              <p className="text-slate-400 font-bold">NEUTRAL</p><p className="text-muted">−25 to 25</p>
            </div>
            <div className="rounded-lg border border-rose-500/20 px-2 py-1.5 bg-rose-500/5">
              <p className="text-rose-400 font-bold">SELL / STRONG_SELL</p><p className="text-muted">≤ −25 / ≤ −60</p>
            </div>
          </div>
        </Node>
      </div>

      <Connector active label="MasterIntelligenceScore table" />

      {/* ── STEP 5: live score table ─────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={ListFilter}
          title="Master Intelligence Scores"
          subtitle="Live DB table — not a separately rebuilt shortlist. Both decision paths below query it directly."
          timing="latest scoring cycle"
          accent="amber"
          wide
          stats={[
            { label: 'Scored (latest cycle)', value: scoredN > 0 ? scoredN : '—', color: 'text-amber-300' },
            { label: 'BUY / STRONG_BUY',   value: buyN > 0 ? buyN : '—',             color: 'text-emerald-400' },
            { label: 'Path A reads',      value: 'top ~10 candidates/cycle',                       color: 'text-slate-200' },
            { label: 'Path B reads',      value: 'last 45 min window',                  color: 'text-slate-200' },
          ]}
        />
      </div>

      <BranchArrow active={isOpen} />

      {/* ── STEP 6: three semi-independent decision paths ────────────────────── */}
      <p className="text-[11px] text-muted uppercase tracking-widest text-center mb-1">
        Three semi-independent engines can each place an order — not one linear funnel
      </p>
      <div className="flex flex-col sm:flex-row gap-3">
        <PathCard
          letter="A" title="Master Intelligence Cycle" Icon={BrainCircuit} accent="emerald" active
          timing="every 15 min, inline with scoring"
          points={[
            'Same cycle that scores the universe also closes SL/TP hits, evaluates its own top ~10 candidates, and executes — no handoff to a separate loop.',
            'DecisionEngine.fuse() → LLM reasoning gate → RiskManagerAgent.can_take_trade() → AgentExecutionManager.execute()',
            'Daily new-entry cap (AGENT_MAX_NEW_ENTRIES_DAY). Logged as PATH: A_inline.',
          ]}
        />
        <PathCard
          letter="B" title="India Trade Loop" Icon={Bot} accent="blue" active
          timing="every 60s, 09:15–16:00 IST"
          points={[
            'Queries MasterIntelligenceScore directly (last 45 min) — the same table Path A scores, read independently.',
            'Runs its own validate_signal / calculate_position_size and its own LLM reasoning-gate call before opening a paper trade.',
            'Logged as PATH: B_live_loop — instrumented side-by-side with Path A for comparison, not dead code.',
          ]}
        />
        <PathCard
          letter="C" title="V4 Event-Driven Discovery Engine" Icon={Newspaper} accent="amber" active={true}
          timing="24/7, running as a systemd --user service"
          points={[
            'Clustering & Deduplication: difflib merges multiple articles (e.g., ET, Mint) into a single Master Event to prevent score inflation.',
            'LLM Categorization: Extracts subcategories, impact horizon, decay half-life, bullish/bearish flags, and mapped entities.',
            'Surprise Engine: Scores the catalyst strength (1-100) vs expectations. Only top clustered candidates proceed to filtering.',
            'Execution: On TAKE, routes through validate_signal() using technicals strictly as a timing filter before paper trading.',
          ]}
        />
      </div>

      <MergeArrow active={isOpen} />

      {/* ── STEP 7: shared reasoning + risk + sizing ─────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={ShieldCheck}
          title="Shared Reasoning Gate, Risk Check &amp; Capital Sizing"
          subtitle="Both Path A and Path B route through the same LLM, risk manager, and sizing formula"
          timing="per candidate"
          accent="cyan"
          wide
          stats={[
            { label: 'Mode',          value: agent?.paper_mode ? 'PAPER' : 'LIVE', color: agent?.paper_mode ? 'text-blue-400' : 'text-emerald-400' },
            { label: 'Open positions',value: portfolio?.open_positions_count ?? '—', color: 'text-slate-200' },
            { label: 'Cash',          value: portfolio?.cash ? `₹${(portfolio.cash/100000).toFixed(1)}L` : '—', color: 'text-slate-200' },
            { label: 'Decisions today',value: agent?.decisions_today ?? 0, color: 'text-slate-200' },
          ]}
        >
          <div className="mt-3 pt-3 border-t border-border text-xs text-muted space-y-1.5">
            <p className="flex items-center gap-1.5"><BrainCircuit size={11} className="text-purple-400 shrink-0" />
              LLM reasoning gate — sole provider is <span className="text-slate-300 font-medium">Mantle / AWS Bedrock gpt-oss-120b</span>.
              No Ollama, no Groq fallback (legacy fallback params are accepted but ignored — every call goes to gpt-oss now).
            </p>
            <p className="flex items-center gap-1.5"><FileSearch size={11} className="text-cyan shrink-0" />
              Every reasoning call is persisted to <span className="text-slate-300 font-medium">LLMReasoningLog</span> / <span className="text-slate-300 font-medium">reasoning_verdicts</span> — full trace visible on the <span className="text-slate-300 font-medium">Agent Log</span> page.
            </p>
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400 shrink-0" />
              Capital sizing (<code className="text-cyan bg-slate-800 px-1 rounded">capital_utilization_size</code>): position weight scales 2%→5% of equity with conviction, damped by VIX (×1.0 at VIX 22 → ×0.5 at VIX 30).
            </p>
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400 shrink-0" />
              Hard caps: 5% of equity per position, 20% minimum cash buffer (≤80% of equity ever deployed), and the trade is still capped so a stop-out never loses more than 1% of equity.
            </p>
            <p className="flex items-center gap-1.5"><ChevronRight size={11} className="text-emerald-400 shrink-0" />
              Wallet balance is DB-configurable (RuntimeConfig.paper_trading_balance) — ₹20L is the current default, not a hardcoded constant.
            </p>
          </div>
        </Node>
      </div>

      {/* ── STEP 8: Decision fork ─────────────────────────────────────────────── */}
      <div className="flex justify-center gap-8 mt-2">
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
            AgentExecutionManager / trade_simulator.open_paper_trade — Zerodha Kite live, or Paper DB in paper mode
          </div>
        </div>

        <div className="flex flex-col items-center gap-2 opacity-50">
          <div className="w-px h-6 bg-border" />
          <div className="rounded-xl border border-border px-4 py-2 flex items-center gap-2">
            <XCircle size={14} className="text-slate-500" />
            <span className="text-sm font-medium text-slate-500">SKIP</span>
          </div>
          <div className="text-[10px] text-muted text-center max-w-[140px]">
            Low conviction, LLM veto, or a risk gate triggered
          </div>
        </div>
      </div>

      <div className="flex justify-center mt-2">
        <div className="relative w-px h-6 bg-border overflow-hidden">
          {isOpen && <div className="absolute w-full bg-amber-400/80 rounded-full"
            style={{ height: 8, animation: 'flowDown 1s infinite linear' }} />}
        </div>
      </div>

      {/* ── STEP 9: Telegram ─────────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <div className="rounded-2xl border border-amber-500/25 bg-amber-500/5 px-6 py-4 flex items-center gap-4 w-full max-w-2xl">
          <div className="p-2.5 rounded-xl border border-amber-500/25 bg-amber-500/10">
            <Bell size={18} className="text-amber-400" />
          </div>
          <div>
            <p className="text-sm font-semibold text-amber-300">Telegram Alert</p>
            <p className="text-xs text-muted mt-0.5">
              Fires on: every BUY order (Path A or B), every breakout/momentum discovery injection, and F&amp;O position open/close —
              symbol, price, qty, score, and the LLM's reasoning summary.
            </p>
          </div>
        </div>
      </div>

      {/* ── timing summary ───────────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-border p-5 mt-4 glass-panel">
        <p className="text-xs font-semibold text-slate-300 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Clock size={12} className="text-cyan" /> Actual celery-beat schedule (IST)
        </p>
        <div className="space-y-2">
          {[
            { time: 'continuous', event: 'Price scan every 30s · live-price backstop refresh every 15s', color: 'text-cyan', dot: 'bg-cyan' },
            { time: 'every 5m', event: 'Narrative/macro intel refresh · breakout screener (NSE-open gated, +60s offset)', color: 'text-emerald-400', dot: 'bg-emerald-400' },
            { time: 'every 15m', event: 'Options chain refresh · Master Intelligence Cycle scoring + Path A inline execution (fires ~45s after each :14/:29/:44/:59 bar close)', color: 'text-purple-400', dot: 'bg-purple-400' },
            { time: 'every 30m', event: 'Momentum discovery scan — runs 24/7, not market-hours gated', color: 'text-slate-300', dot: 'bg-slate-400' },
            { time: '24/7', event: 'News-First Discovery Engine RSS poll — running as a service, TAKE verdicts now route through the standard risk gate into a paper trade', color: 'text-amber-400', dot: 'bg-amber-400' },
            { time: 'every 60s', event: 'India Trade Loop (Path B), 09:15–16:00 IST', color: 'text-blue-400', dot: 'bg-blue-400' },
            { time: '9:00 AM', event: 'hub_universe rebuild (crontab 03:30 UTC)', color: 'text-cyan', dot: 'bg-cyan' },
            { time: '3:25 PM', event: 'Agent EOD reconcile (crontab 09:55 UTC)', color: 'text-slate-300', dot: 'bg-slate-400' },
            { time: '3:45 PM', event: 'F&O expiry sweep, weekdays (crontab 10:15 UTC)', color: 'text-amber-300', dot: 'bg-amber-400' },
          ].map((row, i) => (
            <div key={i} className="flex items-start gap-3">
              <span className="text-[11px] font-mono text-muted w-20 shrink-0 pt-0.5">{row.time}</span>
              <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${row.dot}`} />
              <span className={`text-xs ${row.color}`}>{row.event}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── universe threshold callout ───────────────────────────────────────── */}
      <div className="rounded-2xl border border-cyan/20 p-4 flex gap-4" style={{ background: 'rgba(6,182,212,0.04)' }}>
        <Zap size={20} className="text-cyan shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-semibold text-cyan mb-1">Small-cap visibility fix</p>
          <div className="text-xs text-muted space-y-1">
            <p><span className="text-red-400 font-medium">Before:</span> hub_universe ranked only by turnover ≥ ₹20 Cr/day → sudden small/mid-cap movers were invisible to the scorer.</p>
            <p><span className="text-emerald-400 font-medium">After:</span> threshold lowered to ₹1 Cr/day, plus the breakout screener and momentum discovery inject any qualifying mover in real time regardless of its baseline turnover.</p>
          </div>
        </div>
      </div>
    </div>
  )
}
