import { useState, useEffect, useCallback } from 'react'
import {
  Activity, Zap, TrendingUp,
  Database, BrainCircuit, Bot, ShoppingCart, Bell, Clock,
  CheckCircle2, XCircle, AlertCircle, RefreshCw, ChevronRight,
  Wifi, WifiOff, Shield, Layers, Target, BarChart,
  ArrowUpDown,
} from 'lucide-react'
import { apiFetch, getIndiaMarketStatus } from '../api/client'

function useInterval(fn, ms) {
  useEffect(() => {
    fn()
    const id = setInterval(fn, ms)
    return () => clearInterval(id)
  }, [ms])
}

function Connector({ active = true, label = '' }) {
  return (
    <div className="flex flex-col items-center my-1">
      <div className="flex flex-col items-center">
        <div className="relative w-px h-8 bg-border overflow-hidden">
          {active && (
            <div
              className="absolute w-full bg-cyan/80 rounded-full"
              style={{ height: 8, animation: 'fnoFlowDown 1.2s infinite linear' }}
            />
          )}
        </div>
        {label && <span className="text-[10px] text-muted mt-0.5">{label}</span>}
      </div>
      <style>{`
        @keyframes fnoFlowDown {
          0%   { top: -8px; opacity: 0 }
          20%  { opacity: 1 }
          80%  { opacity: 1 }
          100% { top: 100%; opacity: 0 }
        }
      `}</style>
    </div>
  )
}

function BranchConnector({ active }) {
  return (
    <div className="flex items-end justify-center gap-16 my-1" style={{ height: 44 }}>
      <svg width="520" height="44" viewBox="0 0 520 44" fill="none">
        <line x1="260" y1="0" x2="80"  y2="44" stroke="#334155" strokeWidth="1.5"/>
        <line x1="260" y1="0" x2="260" y2="44" stroke="#334155" strokeWidth="1.5"/>
        <line x1="260" y1="0" x2="440" y2="44" stroke="#334155" strokeWidth="1.5"/>
        {active && (<>
          <circle r="3" fill="#22D3EE" opacity="0.85">
            <animateMotion dur="1s" repeatCount="indefinite" begin="0s" path="M260,2 L80,42"/>
          </circle>
          <circle r="3" fill="#22D3EE" opacity="0.85">
            <animateMotion dur="1s" repeatCount="indefinite" begin="0.33s" path="M260,2 L260,42"/>
          </circle>
          <circle r="3" fill="#22D3EE" opacity="0.85">
            <animateMotion dur="1s" repeatCount="indefinite" begin="0.66s" path="M260,2 L440,42"/>
          </circle>
        </>)}
      </svg>
    </div>
  )
}

function MergeConnector({ active }) {
  return (
    <div className="flex items-center justify-center my-1" style={{ height: 36 }}>
      <svg width="520" height="36" viewBox="0 0 520 36" fill="none">
        <line x1="80"  y1="0" x2="260" y2="36" stroke="#334155" strokeWidth="1.5"/>
        <line x1="260" y1="0" x2="260" y2="36" stroke="#334155" strokeWidth="1.5"/>
        <line x1="440" y1="0" x2="260" y2="36" stroke="#334155" strokeWidth="1.5"/>
        {active && (<>
          <circle r="3" fill="#22D3EE" opacity="0.85">
            <animateMotion dur="0.9s" repeatCount="indefinite" begin="0s" path="M80,2 L260,34"/>
          </circle>
          <circle r="3" fill="#22D3EE" opacity="0.85">
            <animateMotion dur="0.9s" repeatCount="indefinite" begin="0.3s" path="M260,2 L260,34"/>
          </circle>
          <circle r="3" fill="#22D3EE" opacity="0.85">
            <animateMotion dur="0.9s" repeatCount="indefinite" begin="0.6s" path="M440,2 L260,34"/>
          </circle>
        </>)}
      </svg>
    </div>
  )
}

function StatusPill({ ok, label }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full
      ${ok ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`} />
      {label}
    </span>
  )
}

function RegimePill({ state }) {
  const cfg = {
    STRONG_BULL:  { color: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30', dot: 'bg-emerald-400 animate-pulse', label: 'STRONG BULL' },
    MODERATE_BULL:{ color: 'bg-cyan/15 text-cyan border-cyan/30',                      dot: 'bg-cyan animate-pulse',         label: 'MODERATE BULL' },
    SIDEWAYS:     { color: 'bg-amber-500/15 text-amber-300 border-amber-500/30',       dot: 'bg-amber-400',                  label: 'SIDEWAYS' },
    WEAK_BEAR:    { color: 'bg-orange-500/15 text-orange-300 border-orange-500/30',    dot: 'bg-orange-400',                 label: 'WEAK BEAR' },
    STRONG_BEAR:  { color: 'bg-red-500/15 text-red-300 border-red-500/30',             dot: 'bg-red-400 animate-pulse',      label: 'STRONG BEAR' },
    UNKNOWN:      { color: 'bg-slate-500/15 text-slate-400 border-slate-600',          dot: 'bg-slate-500',                  label: 'UNKNOWN' },
  }
  const c = cfg[state] ?? cfg.UNKNOWN
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-bold px-2.5 py-1 rounded-full border ${c.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {c.label}
    </span>
  )
}

function Node({ Icon, title, subtitle, timing, stats = [], accent = 'cyan', status, wide = false, children }) {
  const borders = {
    cyan:    'border-cyan/30',
    emerald: 'border-emerald-500/30',
    blue:    'border-blue-500/30',
    amber:   'border-amber-500/30',
    purple:  'border-purple-500/30',
    rose:    'border-rose-500/30',
    orange:  'border-orange-500/30',
    indigo:  'border-indigo-500/30',
  }
  const glows = {
    cyan:    'rgba(6,182,212,0.04)',
    emerald: 'rgba(16,185,129,0.04)',
    blue:    'rgba(59,130,246,0.04)',
    amber:   'rgba(245,158,11,0.04)',
    purple:  'rgba(168,85,247,0.04)',
    rose:    'rgba(244,63,94,0.04)',
    orange:  'rgba(249,115,22,0.04)',
    indigo:  'rgba(99,102,241,0.04)',
  }
  const icons = {
    cyan:    'text-cyan',
    emerald: 'text-emerald-400',
    blue:    'text-blue-400',
    amber:   'text-amber-400',
    purple:  'text-purple-400',
    rose:    'text-rose-400',
    orange:  'text-orange-400',
    indigo:  'text-indigo-400',
  }
  const bc = borders[accent] ?? 'border-border'
  const bg = glows[accent] ?? 'transparent'
  const ic = icons[accent] ?? 'text-slate-300'

  return (
    <div className={`rounded-2xl border ${bc} p-4 ${wide ? 'w-full max-w-2xl' : 'w-64'}`} style={{ background: bg }}>
      <div className="flex items-start gap-3">
        <div className={`p-2 rounded-xl border ${bc} shrink-0`} style={{ background: bg }}>
          <Icon size={18} className={ic} />
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

export default function FnOPipelineFlow() {
  const [market, setMarket]   = useState(null)
  const [signals, setSignals] = useState(null)
  const [ivRank, setIvRank]   = useState(null)
  const [positions, setPositions] = useState(null)
  const [regime, setRegime]   = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [mkt, sig, iv, pos, reg] = await Promise.allSettled([
        getIndiaMarketStatus(),
        apiFetch('/api/v1/india/fno/signals').catch(() => null),
        apiFetch('/api/v1/india/fno/iv-rank/NIFTY').catch(() => null),
        apiFetch('/api/v1/india/fno/positions').catch(() => null),
        apiFetch('/api/v1/india/regime').catch(() => null),
      ])
      if (mkt.status === 'fulfilled') setMarket(mkt.value)
      if (sig.status === 'fulfilled') setSignals(sig.value?.signals ?? null)
      if (iv.status  === 'fulfilled') setIvRank(iv.value)
      if (pos.status === 'fulfilled') setPositions(pos.value)
      if (reg.status === 'fulfilled') setRegime(reg.value)
    } finally {
      setLoading(false)
    }
  }, [])

  useInterval(load, 30_000)

  const isOpen   = market?.nse_open ?? false
  const nifty    = market?.nifty
  const niftyPct = nifty?.change_pct ?? 0
  const niftyColor = niftyPct >= 0 ? 'text-emerald-400' : 'text-red-400'

  const regimeState   = regime?.state ?? 'UNKNOWN'
  const regimeConf    = regime?.confidence ?? null
  const openFno = Array.isArray(positions) ? positions.length : 0

  const niftyIvr = ivRank?.iv_rank ?? null
  const ivLabel  = niftyIvr == null ? '—' : `${niftyIvr.toFixed(0)}%`
  const ivColor  = niftyIvr == null ? 'text-slate-400' : niftyIvr < 30 ? 'text-cyan' : niftyIvr > 70 ? 'text-rose-400' : 'text-amber-400'
  const ivDesc   = niftyIvr == null ? '' : niftyIvr < 30 ? 'CHEAP' : niftyIvr > 70 ? 'RICH' : 'FAIR'

  const regimeBlocksBull = ['WEAK_BEAR', 'STRONG_BEAR'].includes(regimeState)
  const regimeBlocksBear = ['MODERATE_BULL', 'STRONG_BULL'].includes(regimeState)

  return (
    <div className="max-w-3xl mx-auto space-y-2 pb-12">

      {/* ── Header ───────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-y-3 gap-x-4 mb-6">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-slate-100">F&O Pipeline Flow</h1>
          <p className="text-muted text-sm mt-0.5">How Prajna selects, prices, and executes F&O strategies</p>
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

      {/* ── STEP 1: Regime Gate ──────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Shield}
          title="Market Regime Engine"
          subtitle="5-signal composite gating which F&O strategies are allowed"
          timing="evaluated per trade cycle"
          accent="blue"
          wide
          stats={[
            { label: 'Current Regime', value: regimeState.replace('_', ' '), color:
              regimeState === 'STRONG_BULL' || regimeState === 'MODERATE_BULL' ? 'text-emerald-400' :
              regimeState === 'SIDEWAYS'    ? 'text-amber-400' :
              regimeState === 'WEAK_BEAR'   ? 'text-orange-400' : 'text-red-400' },
            { label: 'Confidence',     value: regimeConf != null ? `${regimeConf.toFixed(0)}%` : '—', color: 'text-cyan' },
            { label: 'Bull Spreads',   value: regimeBlocksBull ? 'BLOCKED' : 'ALLOWED', color: regimeBlocksBull ? 'text-rose-400' : 'text-emerald-400' },
            { label: 'Bear Spreads',   value: regimeBlocksBear ? 'BLOCKED' : 'ALLOWED', color: regimeBlocksBear ? 'text-rose-400' : 'text-emerald-400' },
          ]}
        >
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <RegimePill state={regimeState} />
          </div>
          <div className="mt-3 pt-3 border-t border-border grid grid-cols-1 gap-1 text-xs text-muted">
            {[
              ['EMA Stack',   'EMA20 > EMA50 > EMA200 stacks = bullish trend'],
              ['20d ROC',     'Rate-of-change momentum — positive = accelerating'],
              ['EMA Slope',   'Slope of EMA50 — positive = trend still rising'],
              ['Breadth',     'NSE advance/decline ratio > 0.55 = broad participation'],
              ['India VIX',   'VIX < 18 = calm; 18-24 = caution; >24 = fear (regime scale — the Market Sentiment panel below uses a separate 13/18 VIX scale)'],
            ].map(([k, v]) => (
              <p key={k} className="flex gap-2">
                <span className="text-slate-400 font-medium w-20 shrink-0">{k}</span>
                <span>{v}</span>
              </p>
            ))}
          </div>
          <div className="mt-2 pt-2 border-t border-border text-[11px] text-muted space-y-0.5">
            <p className="flex items-center gap-1.5"><XCircle size={11} className="text-rose-400 shrink-0" /> WEAK_BEAR / STRONG_BEAR → Bull Call Spreads blocked</p>
            <p className="flex items-center gap-1.5"><XCircle size={11} className="text-rose-400 shrink-0" /> MODERATE_BULL / STRONG_BULL → Bear Put Spreads blocked</p>
            <p className="flex items-center gap-1.5"><CheckCircle2 size={11} className="text-emerald-400 shrink-0" /> SIDEWAYS → Iron Condors and Straddles preferred</p>
          </div>
        </Node>
      </div>

      <Connector active={isOpen} />

      {/* ── STEP 2: Signal Engine ────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={BrainCircuit}
          title="7-Factor Composite Signal"
          subtitle="Blends market data into a directional score [-100, +100] per index"
          timing="every trade cycle"
          accent="purple"
          wide
        >
          <div className="mt-3 grid grid-cols-1 gap-1.5">
            {[
              { factor: 'Price Trend + Momentum', weight: '35', color: 'bg-cyan',        desc: 'EMA21 trend × 40 + 5-day momentum × 30 (FINNIFTY proxies off BANKNIFTY candle — bank-dominated index)' },
              { factor: 'PCR Contrarian',          weight: '15', color: 'bg-blue-500',    desc: 'Linear on (PCR − 1.0)/0.4, clamped ±1 — high PCR = contrarian bullish, low = bearish' },
              { factor: 'FII/DII Net Flow',        weight: '15', color: 'bg-emerald-500', desc: '±₹5,000 Cr saturates; positive = institutional buying' },
              { factor: 'Market Breadth',          weight: '10', color: 'bg-indigo-500',  desc: 'NSE advances vs declines normalised ratio' },
              { factor: 'Max Pain Gravity',        weight: '10', color: 'bg-amber-500',   desc: 'OI-weighted strike pinning — spot vs max-pain deviation' },
              { factor: 'News Sentiment',          weight: '10', color: 'bg-orange-500',  desc: 'Average of precomputed score on last 20 NewsItem rows + narrative-engine boost — no LLM call in this factor itself' },
              { factor: 'VIX Damper',              weight:  '5', color: 'bg-purple-500',  desc: 'Multiplier on total score, not additive: VIX <18 → ×1.0; 18-24 → ×0.8; >24 → ×0.6' },
            ].map(f => (
              <div key={f.factor} className="flex items-center gap-3 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${f.color}`} />
                <span className="text-slate-300 font-medium w-36 shrink-0">{f.factor}</span>
                <span className="text-cyan/80 font-bold w-8 shrink-0">{f.weight}</span>
                <span className="text-muted">{f.desc}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-border flex items-center gap-4 text-xs">
            <span className="text-muted">Score:</span>
            <span className="text-red-400 font-bold">−100 SELL</span>
            <div className="flex-1 h-1.5 rounded-full bg-gradient-to-r from-red-500 via-slate-600 to-emerald-500" />
            <span className="text-emerald-400 font-bold">+100 BUY</span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
            <div className="rounded-lg border border-emerald-500/20 px-2 py-1.5 bg-emerald-500/5">
              <p className="text-emerald-400 font-bold">BUY</p>
              <p className="text-muted">score ≥ +12</p>
            </div>
            <div className="rounded-lg border border-slate-600 px-2 py-1.5 bg-slate-800/20">
              <p className="text-slate-400 font-bold">NEUTRAL</p>
              <p className="text-muted">−12 to +12</p>
            </div>
            <div className="rounded-lg border border-rose-500/20 px-2 py-1.5 bg-rose-500/5">
              <p className="text-rose-400 font-bold">SELL</p>
              <p className="text-muted">score ≤ −12</p>
            </div>
          </div>
          {Array.isArray(signals) && signals.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border space-y-1">
              {signals.slice(0, 3).map(s => (
                <div key={s.underlying} className="flex items-center gap-3 text-xs">
                  <span className="text-slate-300 font-bold w-24 shrink-0">{s.underlying}</span>
                  <span className={`font-bold ${s.direction === 'BUY' ? 'text-emerald-400' : s.direction === 'SELL' ? 'text-rose-400' : 'text-slate-400'}`}>
                    {s.direction}
                  </span>
                  <span className="text-muted">conf {s.confidence?.toFixed(0)}%</span>
                  <span className="text-slate-500 ml-auto">score {s.score > 0 ? '+' : ''}{s.score?.toFixed(0)}</span>
                </div>
              ))}
            </div>
          )}
        </Node>
      </div>

      <Connector active={isOpen} label="confidence ≥ 55%" />

      <p className="text-[11px] text-muted uppercase tracking-widest text-center">
        Two independent passes both feed contract resolution below — the composite signal never checks IV-Rank,
        and the volatility pass never checks the composite signal
      </p>

      {/* ── STEP 3a: Directional pass (signal-driven, no IV-Rank input) ─────── */}
      <div className="flex justify-center">
        <div className="rounded-2xl border border-emerald-500/25 p-4 w-full max-w-2xl" style={{ background: 'rgba(16,185,129,0.03)' }}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center border border-emerald-500/25 text-emerald-400 shrink-0">1</span>
            <p className="text-slate-100 font-semibold text-sm">Directional Spread Pass — <code className="text-emerald-400">evaluate_index_options()</code></p>
          </div>
          <p className="text-muted text-xs mb-2">Gate: ENABLE_FNO + ENABLE_OPTIONS · NSE must be open · runs every agent cycle</p>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="rounded-xl border border-emerald-500/20 p-2.5 bg-emerald-500/5">
              <p className="text-emerald-400 font-bold mb-1">Signal = BUY</p>
              <p className="text-slate-200 font-medium">Bull Call Spread</p>
              <p className="text-muted mt-0.5">Blocked if regime ∈ WEAK_BEAR/STRONG_BEAR</p>
            </div>
            <div className="rounded-xl border border-rose-500/20 p-2.5 bg-rose-500/5">
              <p className="text-rose-400 font-bold mb-1">Signal = SELL</p>
              <p className="text-slate-200 font-medium">Bear Put Spread</p>
              <p className="text-muted mt-0.5">Blocked if regime ∈ MODERATE_BULL/STRONG_BULL</p>
            </div>
          </div>
        </div>
      </div>

      {/* ── STEP 3b: Volatility pass (IV-Rank driven, no signal input) ──────── */}
      <div className="flex justify-center">
        <div className="rounded-2xl border border-amber-500/25 p-4 w-full max-w-2xl" style={{ background: 'rgba(245,158,11,0.03)' }}>
          <div className="flex items-center gap-3 mb-2">
            <span className="text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center border border-amber-500/25 text-amber-400 shrink-0">2</span>
            <div className="flex-1">
              <p className="text-slate-100 font-semibold text-sm">Volatility Pass — <code className="text-amber-400">evaluate_volatility()</code></p>
              <p className="text-muted text-xs">Gate: ENABLE_FNO + ENABLE_OPTIONS + <span className="text-amber-400 font-medium">FNO_VOL_ENABLED</span> (separate flag, code default off — enabled on this host)</p>
            </div>
            <div className="text-right shrink-0">
              <p className={`text-lg font-bold tabular-nums ${ivColor}`}>{ivLabel}</p>
              {ivDesc && <p className={`text-[10px] font-bold ${ivColor}`}>{ivDesc}</p>}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2 text-[11px]">
            <div className="rounded-xl border border-cyan/25 p-2.5 bg-cyan/5">
              <p className="text-cyan font-bold mb-1">IV-Rank &lt; 30</p>
              <p className="text-slate-200 font-medium">Long Straddle</p>
              <p className="text-muted mt-0.5">Cheap options — buy ATM CE + PE. SL −50%/TP +100% per leg.</p>
            </div>
            <div className="rounded-xl border border-slate-600 p-2.5 bg-slate-800/20">
              <p className="text-slate-400 font-bold mb-1">IV-Rank 30–70</p>
              <p className="text-slate-200 font-medium">No vol trade</p>
              <p className="text-muted mt-0.5">Fair vol — this pass skips the underlying entirely</p>
            </div>
            <div className="rounded-xl border border-rose-500/25 p-2.5 bg-rose-500/5">
              <p className="text-rose-400 font-bold mb-1">IV-Rank &gt; 70</p>
              <p className="text-slate-200 font-medium">Iron Condor</p>
              <p className="text-muted mt-0.5">Rich options — sell OTM strangle with wings for credit.</p>
            </div>
          </div>
        </div>
      </div>

      <BranchConnector active={isOpen} />

      {/* ── STEP 4: Four concrete strategy specs ─────────────────────────────── */}
      <div className="flex gap-3">
        {/* Bull Call Spread */}
        <div className="flex-1 rounded-xl border border-emerald-500/25 p-3" style={{ background: 'rgba(16,185,129,0.03)' }}>
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp size={14} className="text-emerald-400" />
            <span className="text-xs font-bold text-emerald-300">BULL/BEAR SPREAD</span>
          </div>
          <div className="space-y-1 text-[11px] text-muted">
            <p className="flex gap-1"><span className="text-emerald-400 w-14 shrink-0">BUY leg:</span> ATM CE or PE</p>
            <p className="flex gap-1"><span className="text-rose-400 w-14 shrink-0">SELL leg:</span> ATM ± width</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">Width:</span> 200pts (500 for BANKNIFTY/SENSEX)</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">Net debit:</span> buy_prem − sell_prem</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">Exit:</span> TP 50% of max profit · SL 80% of max loss</p>
          </div>
        </div>

        {/* Long Straddle */}
        <div className="flex-1 rounded-xl border border-cyan/25 p-3" style={{ background: 'rgba(6,182,212,0.03)' }}>
          <div className="flex items-center gap-2 mb-2">
            <ArrowUpDown size={14} className="text-cyan" />
            <span className="text-xs font-bold text-cyan">LONG STRADDLE</span>
          </div>
          <div className="space-y-1 text-[11px] text-muted">
            <p className="flex gap-1"><span className="text-emerald-400 w-14 shrink-0">CE leg:</span> Buy 1 lot ATM Call</p>
            <p className="flex gap-1"><span className="text-emerald-400 w-14 shrink-0">PE leg:</span> Buy 1 lot ATM Put</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">Trigger:</span> IV-Rank &lt; 30 (cheap vol)</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">SL:</span> −50% per leg premium</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">TP:</span> +100% per leg premium</p>
          </div>
        </div>

        {/* Iron Condor */}
        <div className="flex-1 rounded-xl border border-rose-500/25 p-3" style={{ background: 'rgba(244,63,94,0.03)' }}>
          <div className="flex items-center gap-2 mb-2">
            <Layers size={14} className="text-rose-400" />
            <span className="text-xs font-bold text-rose-300">IRON CONDOR</span>
          </div>
          <div className="space-y-1 text-[11px] text-muted">
            <p className="flex gap-1"><span className="text-rose-400 w-14 shrink-0">SELL:</span> ATM ± short width</p>
            <p className="flex gap-1"><span className="text-emerald-400 w-14 shrink-0">BUY:</span> ATM ± long width</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">Widths:</span> 200/400 (500/1000 BANKNIFTY)</p>
            <p className="flex gap-1"><span className="text-slate-300 w-14 shrink-0">Net credit:</span> collected upfront</p>
            <p className="flex gap-1"><span className="text-amber-400 w-14 shrink-0">Exit:</span> no SL/TP set — held to expiry sweep</p>
          </div>
        </div>
      </div>

      <MergeConnector active={isOpen} />

      {/* ── STEP 5: Contract Resolution ─────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Database}
          title="Contract Resolution"
          subtitle="Map signal + strike → concrete tradeable NFO symbol"
          timing="per trade attempt"
          accent="blue"
          wide
        >
          <div className="mt-3 space-y-2">
            <div className="rounded-lg border border-blue-500/20 p-3 bg-blue-500/5">
              <p className="text-blue-400 font-semibold text-xs mb-1.5">Path 1 — Kite Instrument Master (live login)</p>
              <div className="space-y-0.5 text-[11px] text-muted">
                <p><span className="text-slate-300">Query:</span> NFO × underlying × CE/PE → filter by expiry nearest DTE=21</p>
                <p><span className="text-slate-300">ATM pick:</span> strike closest to spot; width applied for spread sell leg</p>
                <p><span className="text-slate-300">Min DTE:</span> 2 days (avoids expiry-day gamma risk)</p>
              </div>
            </div>
            <div className="rounded-lg border border-amber-500/20 p-3 bg-amber-500/5">
              <p className="text-amber-400 font-semibold text-xs mb-1.5">Path 2 — OptionContractSnapshot (paper fallback)</p>
              <div className="space-y-0.5 text-[11px] text-muted">
                <p><span className="text-slate-300">Source:</span> Live NSE chain crawled into DB snapshots</p>
                <p><span className="text-slate-300">Symbol:</span> Synthesized — e.g. NIFTY26JUN1524200CE</p>
                <p><span className="text-slate-300">Lot sizes:</span> Hardcoded per underlying from config</p>
              </div>
            </div>
          </div>
          <div className="mt-2 text-[11px] text-muted flex items-center gap-1.5">
            <AlertCircle size={11} className="text-amber-400 shrink-0" />
            If both paths fail → trade is skipped silently (no error)
          </div>
        </Node>
      </div>

      <Connector active={isOpen} />

      {/* ── STEP 6: Premium Lookup ───────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Activity}
          title="Premium Lookup (4-tier Cascade)"
          subtitle="Best available real-time price for each option contract"
          timing="per contract per trade"
          accent="cyan"
          wide
        >
          <div className="mt-3 space-y-2">
            {[
              { tier: '1', label: 'WebSocket PRICE_CACHE', color: 'text-emerald-400', border: 'border-emerald-500/20', bg: 'bg-emerald-500/5', desc: 'Live Zerodha ticker feed — fastest, already subscribed, zero latency' },
              { tier: '2', label: 'Kite REST LTP',         color: 'text-cyan',        border: 'border-cyan/20',         bg: 'bg-cyan/5',        desc: 'Direct API call to Kite for the exact NFO contract last_price' },
              { tier: '3', label: 'DB Snapshot LTP',       color: 'text-amber-400',   border: 'border-amber-500/20',   bg: 'bg-amber-500/5',   desc: 'Latest crawled LTP from OptionContractSnapshot (may be minutes old)' },
              { tier: '4', label: 'Black-Scholes Reprice', color: 'text-purple-400',  border: 'border-purple-500/20',  bg: 'bg-purple-500/5',  desc: 'scipy BSM with spot + IV from snapshot; greeks available here too' },
            ].map(t => (
              <div key={t.tier} className={`flex items-start gap-3 rounded-lg border ${t.border} p-2.5 ${t.bg}`}>
                <span className={`text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center border ${t.border} shrink-0 ${t.color}`}>{t.tier}</span>
                <div>
                  <p className={`text-xs font-semibold ${t.color}`}>{t.label}</p>
                  <p className="text-[11px] text-muted mt-0.5">{t.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </Node>
      </div>

      <Connector active={isOpen} />

      {/* ── STEP 7: Sizing + Margin ──────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={BarChart}
          title="Lot Sizing + Margin Calculation"
          subtitle="Risk-budget sizing with live Zerodha basket API for exact margin"
          timing="per trade"
          accent="emerald"
          wide
        >
          <div className="mt-3 grid grid-cols-1 gap-2 text-xs">
            <div className="rounded-lg border border-border/60 p-3" style={{ background: 'rgba(255,255,255,0.02)' }}>
              <p className="text-slate-200 font-semibold mb-1.5">Lot Sizing Formula</p>
              <div className="space-y-1 text-muted font-mono text-[11px]">
                <p>risk_budget = equity × AGENT_MAX_RISK_PER_TRADE</p>
                <p>risk_per_lot = net_premium × lot_size   <span className="text-slate-500">(spread)</span></p>
                <p>lots = risk_budget ÷ risk_per_lot</p>
                <p>lots = max(1, min(lots, FNO_MAX_LOTS_PER_TRADE))</p>
              </div>
            </div>
            <div className="rounded-lg border border-border/60 p-3" style={{ background: 'rgba(255,255,255,0.02)' }}>
              <p className="text-slate-200 font-semibold mb-1.5">Spread Margin (3-tier)</p>
              <div className="space-y-1.5 text-[11px]">
                <div className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold shrink-0">Best:</span>
                  <span className="text-muted">Zerodha <code className="text-cyan bg-slate-800 px-1 rounded">basket_order_margins</code> API → <code className="text-cyan">initial.total</code></span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-amber-400 font-bold shrink-0">Fallback:</span>
                  <span className="text-muted font-mono">buy_premium×qty + spot×lots×lot_size×2%</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-slate-400 font-bold shrink-0">Futures:</span>
                  <span className="text-muted font-mono">notional × 18% SPAN approx</span>
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <div className="rounded-lg border border-rose-500/20 p-2.5 bg-rose-500/5">
                <p className="text-rose-400 font-bold mb-0.5">Hard Guard</p>
                <p className="text-muted">margin_blocked &gt; AGENT_MAX_POSITION_WEIGHT × equity × 1.1 → reject</p>
              </div>
              <div className="rounded-lg border border-amber-500/20 p-2.5 bg-amber-500/5">
                <p className="text-amber-400 font-bold mb-0.5">Auto Downsize</p>
                <p className="text-muted">If margin &gt; equity → reduce lots until it fits; min 1 lot</p>
              </div>
            </div>
          </div>
        </Node>
      </div>

      <Connector active={isOpen} />

      {/* ── STEP 8: Execution ────────────────────────────────────────────────── */}
      <div className="flex justify-center">
        <Node
          Icon={Bot}
          title="Paper Execution Engine"
          subtitle="Writes trade records, deducts wallet margin, fires Telegram alert"
          timing="on decision"
          accent="amber"
          status={isOpen}
          wide
          stats={[
            { label: 'Open F&O positions', value: openFno > 0 ? openFno : '0', color: 'text-cyan' },
            { label: 'Mode',               value: 'PAPER',                       color: 'text-blue-400' },
            { label: 'Duplicate guard',    value: '1 per underlying',             color: 'text-slate-200' },
            { label: 'Margin source',      value: 'VirtualWallet',               color: 'text-slate-200' },
          ]}
        >
          <div className="mt-3 pt-3 border-t border-border space-y-1 text-[11px] text-muted">
            {[
              ['Hard guard',       'Reject if cost > 110% of max position weight'],
              ['Duplicate guard',  'Skip if open position already exists for same underlying + option_type'],
              ['Write PaperTrade', 'Insert row: symbol, direction, premium, SL/TP, lots, margin'],
              ['Write OpenPosition','Mirror row with unrealised_pnl = 0'],
              ['Deduct wallet',    'VirtualWallet.deduct_margin — rollback both rows on failure'],
              ['DB commit',        'Single transaction; failure → full rollback, nothing written'],
              ['Telegram alert',   'Fires after commit: premium, lots, SL, TP, max profit/loss, breakeven'],
            ].map(([k, v]) => (
              <p key={k} className="flex items-start gap-1.5">
                <ChevronRight size={11} className="text-amber-400 mt-0.5 shrink-0" />
                <span><span className="text-slate-300 font-medium">{k}:</span> {v}</span>
              </p>
            ))}
          </div>
        </Node>
      </div>

      {/* ── STEP 9: SL/TP Monitoring ─────────────────────────────────────────── */}
      <Connector active label="continuous" />

      <div className="flex justify-center">
        <div className="rounded-2xl border border-border p-4 w-full max-w-2xl" style={{ background: 'rgba(255,255,255,0.015)' }}>
          <p className="text-[11px] text-muted uppercase tracking-widest mb-3 flex items-center gap-1.5">
            <Activity size={11} /> Open Position Monitoring — Continuous (exit rule differs per strategy)
          </p>
          <div className="flex gap-3">
            {[
              {
                Icon: Target,
                accent: 'emerald',
                title: 'Mark-to-Market',
                subtitle: 'WebSocket → REST → snapshot → BS reprice every price-tick. Updates unrealised_pnl for every open leg.',
              },
              {
                Icon: Shield,
                accent: 'rose',
                title: 'SL / TP Check',
                subtitle: 'Spreads: TP 50% of max profit, SL 80% of max loss (monitor_spread_exits). Straddle: SL −50%/TP +100% per leg. Iron Condor: no SL/TP set — rides to expiry.',
              },
              {
                Icon: Clock,
                accent: 'amber',
                title: 'Expiry Sweep',
                subtitle: 'Daily 3:45 PM IST, weekdays: settle at intrinsic (options) or spot (futures). Margin returned to wallet.',
              },
            ].map((item, i) => {
              const borders = { emerald: 'border-emerald-500/25', rose: 'border-rose-500/25', amber: 'border-amber-500/25' }
              const icons   = { emerald: 'text-emerald-400',       rose: 'text-rose-400',       amber: 'text-amber-400' }
              return (
                <div key={i} className={`flex-1 rounded-xl border ${borders[item.accent]} p-3`}
                  style={{ background: 'rgba(255,255,255,0.025)', minWidth: 0 }}>
                  <div className="flex items-center gap-2 mb-1">
                    <item.Icon size={14} className={icons[item.accent]} />
                    <span className="text-xs font-semibold text-slate-200 truncate">{item.title}</span>
                  </div>
                  <p className="text-[11px] text-muted leading-snug">{item.subtitle}</p>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <Connector active label="on exit" />

      {/* ── STEP 10: Telegram + Outcome ──────────────────────────────────────── */}
      <div className="flex justify-center gap-4">
        <div className="rounded-2xl border border-emerald-500/25 bg-emerald-500/5 px-5 py-4 flex items-center gap-4 flex-1">
          <div className="p-2.5 rounded-xl border border-emerald-500/25 bg-emerald-500/10">
            <ShoppingCart size={18} className="text-emerald-400" />
          </div>
          <div>
            <p className="text-sm font-semibold text-emerald-300">Trade Closed — Profit</p>
            <p className="text-xs text-muted mt-0.5">Premium × qty returned; margin freed; P&L booked in PaperTrade</p>
          </div>
        </div>
        <div className="rounded-2xl border border-rose-500/25 bg-rose-500/5 px-5 py-4 flex items-center gap-4 flex-1">
          <div className="p-2.5 rounded-xl border border-rose-500/25 bg-rose-500/10">
            <XCircle size={18} className="text-rose-400" />
          </div>
          <div>
            <p className="text-sm font-semibold text-rose-300">Trade Closed — Stop-Loss</p>
            <p className="text-xs text-muted mt-0.5">SL fraction depends on strategy (see monitoring rule above); max loss realised = net debit paid</p>
          </div>
        </div>
      </div>

      <div className="flex justify-center mt-2">
        <div className="relative w-px h-6 bg-border overflow-hidden">
          {isOpen && <div className="absolute w-full bg-amber-400/80 rounded-full"
            style={{ height: 8, animation: 'fnoFlowDown 1s infinite linear' }} />}
        </div>
      </div>

      <div className="flex justify-center">
        <div className="rounded-2xl border border-amber-500/25 bg-amber-500/5 px-6 py-4 flex items-center gap-4 w-full max-w-2xl">
          <div className="p-2.5 rounded-xl border border-amber-500/25 bg-amber-500/10">
            <Bell size={18} className="text-amber-400" />
          </div>
          <div>
            <p className="text-sm font-semibold text-amber-300">Telegram Alert on Every Event</p>
            <p className="text-xs text-muted mt-0.5">
              Open: premium, lots, SL/TP, max profit/max loss, breakeven, conviction %.<br />
              Close: actual P&L, reason (SL / TP / expiry), final margin returned.
            </p>
          </div>
        </div>
      </div>

      {/* ── Timing Summary ───────────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-border p-5 mt-4 glass-panel">
        <p className="text-xs font-semibold text-slate-300 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Clock size={12} className="text-cyan" /> F&O Strategy Reference
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-muted uppercase tracking-wider">
                <th className="text-left pb-2 pr-4">Strategy</th>
                <th className="text-left pb-2 pr-4">Trigger</th>
                <th className="text-left pb-2 pr-4">Risk</th>
                <th className="text-left pb-2">Reward</th>
              </tr>
            </thead>
            <tbody className="space-y-1">
              {[
                { name: 'Bull Call Spread', trigger: 'ENABLE_OPTIONS · BUY signal, conf ≥55, regime ≠ bear',      risk: 'Net debit × qty',             reward: '(Width − net debit) × qty', color: 'text-emerald-400' },
                { name: 'Bear Put Spread',  trigger: 'ENABLE_OPTIONS · SELL signal, conf ≥55, regime ≠ bull',     risk: 'Net debit × qty',             reward: '(Width − net debit) × qty', color: 'text-rose-400' },
                { name: 'Long Straddle',    trigger: 'ENABLE_OPTIONS + FNO_VOL_ENABLED · IV-Rank < 30',                    risk: 'Total premium (both legs)',   reward: 'Unlimited (big move)',       color: 'text-cyan' },
                { name: 'Iron Condor',      trigger: 'ENABLE_OPTIONS + FNO_VOL_ENABLED · IV-Rank > 70',                    risk: '(Wing width − credit) × qty', reward: 'Net credit × qty',           color: 'text-purple-400' },
                { name: 'Portfolio Hedge',  trigger: 'ENABLE_OPTIONS + FNO_HEDGE_ENABLED · equity book >10% + NIFTY bearish',     risk: 'PE premium debit',            reward: 'Hedge against drawdown',     color: 'text-amber-400' },
                { name: 'Index Futures',    trigger: 'ENABLE_FUTURES + BUY/SELL signal', risk: '1.5% stop × notional',        reward: '3% target × notional',       color: 'text-blue-400' },
              ].map((r, i) => (
                <tr key={i} className="border-t border-border/40">
                  <td className={`py-1.5 pr-4 font-semibold ${r.color}`}>{r.name}</td>
                  <td className="py-1.5 pr-4 text-muted">{r.trigger}</td>
                  <td className="py-1.5 pr-4 text-slate-300">{r.risk}</td>
                  <td className="py-1.5 text-slate-300">{r.reward}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Greeks + Config ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl border border-indigo-500/25 p-4" style={{ background: 'rgba(99,102,241,0.03)' }}>
          <p className="text-xs font-semibold text-indigo-300 mb-3 flex items-center gap-2">
            <BrainCircuit size={12} /> Greeks Engine (Black-Scholes)
          </p>
          <div className="space-y-1 text-[11px] text-muted">
            <p><span className="text-indigo-400 font-medium w-14 inline-block">Delta</span> Directional exposure per ₹1 move</p>
            <p><span className="text-indigo-400 font-medium w-14 inline-block">Gamma</span> Rate of delta change</p>
            <p><span className="text-indigo-400 font-medium w-14 inline-block">Vega</span> P&L per 1% IV move</p>
            <p><span className="text-indigo-400 font-medium w-14 inline-block">Theta</span> Daily time decay (₹/day)</p>
            <p><span className="text-indigo-400 font-medium w-14 inline-block">IV solve</span> Brent's method on market price</p>
          </div>
        </div>
        <div className="rounded-2xl border border-slate-600/50 p-4" style={{ background: 'rgba(255,255,255,0.015)' }}>
          <p className="text-xs font-semibold text-slate-300 mb-3 flex items-center gap-2">
            <Zap size={12} className="text-cyan" /> Key Config Flags
          </p>
          <div className="space-y-1 text-[11px] text-muted font-mono">
            <p><span className="text-cyan">ENABLE_FNO</span> = master gate (code default off — <span className="text-emerald-400">on</span> on this host)</p>
            <p><span className="text-cyan">ENABLE_OPTIONS</span> = spreads (code default off — <span className="text-emerald-400">on</span> on this host)</p>
            <p><span className="text-cyan">ENABLE_FUTURES</span> = index futures (code default off — <span className="text-emerald-400">on</span> on this host)</p>
            <p><span className="text-cyan">FNO_VOL_ENABLED</span> = straddle/condor pass (code default off — <span className="text-emerald-400">on</span> on this host)</p>
            <p><span className="text-cyan">FNO_HEDGE_ENABLED</span> = portfolio hedge (code default off — <span className="text-emerald-400">on</span> on this host)</p>
            <p><span className="text-cyan">FNO_DEFAULT_DTE</span> = 21 days</p>
            <p><span className="text-cyan">FNO_MAX_LOTS_PER_TRADE</span> = 10</p>
            <p className="text-muted/70 not-italic normal-case">confidence gate = 55% — hardcoded literal in selection.py, not an actual settings field despite the name</p>
          </div>
        </div>
      </div>

    </div>
  )
}
