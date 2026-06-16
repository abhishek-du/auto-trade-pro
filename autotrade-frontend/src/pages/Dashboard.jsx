import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  IndianRupee, TrendingUp, TrendingDown, Activity, Zap, ArrowUpRight,
  Wallet, Bot, Gauge, Flame, BarChart3, Building2, ArrowRight,
} from 'lucide-react';
import CandlestickChart from '../components/CandlestickChart';
import LoadingSpinner from '../components/LoadingSpinner';
import SectorHeatmapWidget from '../components/heatmap/SectorHeatmapWidget';
import UpcomingEventsWidget from '../components/calendar/UpcomingEventsWidget';
import { usePortfolio } from '../hooks/usePortfolio';
import { useAgent } from '../hooks/useAgent';
import { apiFetch } from '../api/client';

/* ──────────────────────────────────────────────────────────────────────────
   Formatting helpers
─────────────────────────────────────────────────────────────────────────── */
const inr = (n, d = 2) =>
  n == null || isNaN(n) ? '—'
  : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

function rupeeShort(n) {
  const v = Number(n ?? 0); const a = Math.abs(v); const s = v < 0 ? '-' : '';
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(2)} L`;
  return `${s}₹${a.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

const pct = (n) => (n == null || isNaN(n) ? '—' : `${n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%`);

/* ──────────────────────────────────────────────────────────────────────────
   Data hooks
─────────────────────────────────────────────────────────────────────────── */
function usePolledFetch(path, interval = 30000, transform = (x) => x) {
  const [data, setData] = useState(null);
  const fetchIt = useCallback(async () => {
    try { setData(transform(await apiFetch(path))); } catch { /* keep last */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  useEffect(() => {
    fetchIt();
    const id = setInterval(fetchIt, interval);
    return () => clearInterval(id);
  }, [fetchIt, interval]);
  return data;
}

/* Adaptive poll: fast cadence while a condition (e.g. market open) holds,
   slow cadence otherwise. The `isFast` reader runs against the latest data. */
function useAdaptiveFetch(path, fastMs, slowMs, isFast, transform = (x) => x) {
  const [data, setData] = useState(null);
  const dataRef = useRef(null);
  const fetchIt = useCallback(async () => {
    try { const d = transform(await apiFetch(path)); dataRef.current = d; setData(d); }
    catch { /* keep last */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  useEffect(() => {
    let timer;
    const tick = async () => {
      await fetchIt();
      const ms = isFast(dataRef.current) ? fastMs : slowMs;
      timer = setTimeout(tick, ms);
    };
    tick();
    return () => clearTimeout(timer);
  }, [fetchIt, fastMs, slowMs, isFast]);
  return data;
}

/* ──────────────────────────────────────────────────────────────────────────
   Shared UI primitives
─────────────────────────────────────────────────────────────────────────── */
function Card({ children, className = '', pad = 'p-4' }) {
  return (
    <div className={`rounded-2xl border border-border ${pad} ${className}`}
      style={{ background: 'linear-gradient(155deg,#0F1829 0%,#101B2E 100%)' }}>
      {children}
    </div>
  );
}

function SectionTitle({ icon: Icon, children, action }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        {Icon && <Icon size={14} className="text-cyan" />}
        <h2 className="text-slate-100 text-sm font-semibold">{children}</h2>
      </div>
      {action}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   1 ── Market index ticker strip
─────────────────────────────────────────────────────────────────────────── */
function IndexTile({ name, sub, value, change, changePct, vix }) {
  const up = (changePct ?? 0) >= 0;
  const accent = vix
    ? (value < 15 ? 'text-profit' : value > 20 ? 'text-loss' : 'text-warn')
    : up ? 'text-profit' : 'text-loss';
  const pill = vix
    ? (value < 15 ? 'bg-profit/12 text-profit' : value > 20 ? 'bg-loss/12 text-loss' : 'bg-warn/12 text-warn')
    : up ? 'bg-profit/12 text-profit' : 'bg-loss/12 text-loss';
  return (
    <div className="rounded-xl border border-border px-4 py-3 flex flex-col gap-1.5 min-w-0"
      style={{ background: 'linear-gradient(135deg,#101B2E,#0C1422)' }}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-slate-200 text-xs font-bold truncate">{name}</p>
          <p className="text-muted text-[10px] uppercase tracking-wide">{sub}</p>
        </div>
        <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded ${pill} shrink-0`}>
          {vix ? (value < 15 ? 'LOW' : value > 20 ? 'HIGH' : 'MOD') : `${up ? '▲' : '▼'} ${Math.abs(changePct ?? 0).toFixed(2)}%`}
        </span>
      </div>
      <p className={`tabular-nums font-extrabold text-xl leading-none ${accent}`}>
        {vix ? inr(value, 2) : inr(value, 2)}
      </p>
      {!vix && (
        <p className={`text-[11px] tabular-nums ${up ? 'text-profit/70' : 'text-loss/70'}`}>
          {up ? '+' : '−'}{inr(Math.abs(change ?? 0), 2)} today
        </p>
      )}
      {vix && <p className="text-[11px] text-muted">India volatility index</p>}
    </div>
  );
}

function IndexStrip() {
  // Adaptive: poll every 3s while the market is open (live ticks), 30s when closed.
  const mkt = useAdaptiveFetch(
    '/api/v1/india/market-status', 3000, 30000, (d) => !!d?.nse_open,
  );
  const nseOpen = mkt?.nse_open;
  return (
    <div>
      <div className="flex items-center gap-2 mb-2 px-0.5">
        <span className={`w-2 h-2 rounded-full ${nseOpen ? 'bg-profit animate-pulse' : 'bg-loss'}`} />
        {/* LIVE vs CLOSED data badge */}
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${nseOpen ? 'bg-profit/20 text-profit' : 'bg-slate-600/30 text-slate-400'}`}>
          {nseOpen ? '🟢 LIVE' : '● CLOSED'}
        </span>
        <span className="text-xs text-muted">
          NSE <span className={nseOpen ? 'text-profit font-semibold' : 'text-loss font-semibold'}>{nseOpen ? 'OPEN' : 'CLOSED'}</span>
          {mkt?.ist_time && <span className="text-muted/70"> · {mkt.ist_time.split(' ')[1]} IST</span>}
          {nseOpen
            ? <span className="text-muted/60"> · streaming from Zerodha</span>
            : <span className="text-muted/60"> · showing last close</span>}
        </span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <IndexTile name="NIFTY 50"   sub="NSE Index"   value={mkt?.nifty?.price}      change={mkt?.nifty?.change}      changePct={mkt?.nifty?.change_pct} />
        <IndexTile name="BANK NIFTY" sub="NSE Banking" value={mkt?.bank_nifty?.price} change={mkt?.bank_nifty?.change} changePct={mkt?.bank_nifty?.change_pct} />
        <IndexTile name="SENSEX"     sub="BSE 30"      value={mkt?.sensex?.price}     change={mkt?.sensex?.change}     changePct={mkt?.sensex?.change_pct} />
        <IndexTile name="INDIA VIX"  sub="Fear gauge"  value={mkt?.india_vix} vix />
      </div>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   2 ── Portfolio + Agent hero
─────────────────────────────────────────────────────────────────────────── */
function StatChip({ label, value, tone = 'text-slate-200' }) {
  return (
    <div className="bg-white/[0.03] border border-border rounded-lg px-3 py-2 min-w-0">
      <p className="text-muted text-[10px] uppercase tracking-wide truncate">{label}</p>
      <p className={`text-sm font-bold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

function PortfolioHero({ portfolio }) {
  const { status } = useAgent();
  // equity = total portfolio value (cash + positions + unrealised).
  // balance = cash remaining after trade deductions (may be much lower when positions are open).
  const equity     = portfolio?.equity   ?? portfolio?.balance ?? 0;
  const cash       = portfolio?.balance  ?? 0;
  const roi        = portfolio?.roi_percent ?? 0;
  const realised   = portfolio?.realised_pnl   ?? 0;
  const unrealised = portfolio?.unrealised_pnl ?? 0;
  const totalPnl   = realised + unrealised;
  const winRate    = portfolio?.win_rate ?? 0;
  const trades     = portfolio?.total_trades ?? 0;
  const pnlUp      = totalPnl >= 0;

  const agentOn   = !!status?.enabled;
  const paper     = status?.paper_mode !== false;
  const openPos   = portfolio?.positions?.length ?? 0;
  const decisions = status?.decisions_today ?? 0;

  return (
    <Card className="flex flex-col h-full" pad="p-5">
      {/* Header line */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Wallet size={14} className="text-cyan" />
            <span className="text-muted text-[11px] font-semibold uppercase tracking-widest">Paper Portfolio</span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-warn/12 text-warn border border-warn/25">VIRTUAL</span>
          </div>
          <p className="text-slate-50 text-3xl font-extrabold tabular-nums mt-1.5">{rupeeShort(equity)}</p>
          <p className="text-muted text-[11px] tabular-nums mt-0.5">
            {rupeeShort(cash)} cash free
          </p>
          <p className={`text-xs font-semibold tabular-nums mt-0.5 ${pnlUp ? 'text-profit' : 'text-loss'}`}>
            {pct(roi)} all-time · {pnlUp ? '+' : ''}{rupeeShort(totalPnl)} P&L
          </p>
        </div>
        {/* Agent badge */}
        <Link to="/agent" className="text-right group">
          <div className="flex items-center gap-1.5 justify-end">
            <Bot size={13} className={agentOn ? 'text-profit' : 'text-muted'} />
            <span className={`text-[11px] font-bold ${agentOn ? 'text-profit' : 'text-muted'}`}>
              AI AGENT {agentOn ? 'ON' : 'OFF'}
            </span>
            <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-white/5 text-muted border border-border">
              {paper ? 'PAPER' : 'LIVE'}
            </span>
          </div>
          <p className="text-muted text-[10px] mt-0.5 group-hover:text-cyan transition-colors flex items-center gap-0.5 justify-end">
            {decisions} decisions today <ArrowUpRight size={10} />
          </p>
        </Link>
      </div>

      {/* Stat chips */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-4">
        <StatChip label="Realised" value={rupeeShort(realised)} tone={realised >= 0 ? 'text-profit' : 'text-loss'} />
        <StatChip label="Unrealised" value={rupeeShort(unrealised)} tone={unrealised >= 0 ? 'text-profit' : 'text-loss'} />
        <StatChip label="Win Rate" value={trades > 0 ? `${winRate.toFixed(0)}%` : '—'} />
        <StatChip label="Open Positions" value={openPos} />
      </div>

      {/* Equity chart */}
      <div className="mt-4 flex-1 min-h-[200px]">
        <CandlestickChart />
      </div>
    </Card>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   3 ── Market pulse (breadth + FII/DII + VIX)
─────────────────────────────────────────────────────────────────────────── */
function MarketPulse() {
  const breadth = usePolledFetch('/api/v1/india/breadth', 120000);
  const fii     = usePolledFetch('/api/v1/india/fii-dii', 300000);
  const mkt     = usePolledFetch('/api/v1/india/market-status', 60000);

  const nse  = breadth?.nse || breadth || {};
  const adv  = nse.nse_advances ?? nse.advances ?? 0;
  const dec  = nse.nse_declines ?? nse.declines ?? 0;
  const unc  = nse.nse_unchanged ?? nse.unchanged ?? 0;
  const mood = nse.nse_market_mood ?? nse.market_mood ?? 'NEUTRAL';
  const total = Math.max(adv + dec + unc, 1);
  const advPct = (adv / total) * 100;

  const moodTone = {
    STRONGLY_BULLISH: 'text-emerald-400', BULLISH: 'text-profit',
    NEUTRAL: 'text-slate-400', BEARISH: 'text-loss', STRONGLY_BEARISH: 'text-red-400',
  }[mood] || 'text-slate-400';

  const fiiNet = fii?.today?.fii_net ?? 0;
  const diiNet = fii?.today?.dii_net ?? 0;
  const vix    = mkt?.india_vix;

  return (
    <Card className="flex flex-col h-full" pad="p-5">
      <SectionTitle icon={Gauge}>Market Pulse</SectionTitle>

      {/* Breadth */}
      <div>
        <div className="flex items-center justify-between text-xs mb-1.5">
          <span className="text-profit font-semibold tabular-nums">▲ {adv} adv</span>
          <span className={`font-bold ${moodTone}`}>{(mood || 'NEUTRAL').replace('_', ' ')}</span>
          <span className="text-loss font-semibold tabular-nums">{dec} dec ▼</span>
        </div>
        <div className="h-2.5 rounded-full overflow-hidden flex bg-loss/30">
          <div className="bg-profit h-full" style={{ width: `${advPct}%` }} />
        </div>
        <p className="text-muted text-[10px] mt-1 text-center">{total} NSE stocks tracked · {unc} unchanged</p>
      </div>

      {/* FII / DII */}
      <div className="mt-4 pt-4 border-t border-border">
        <p className="text-muted text-[10px] uppercase tracking-wide mb-2">Institutional flows (₹ Cr, latest)</p>
        <div className="grid grid-cols-2 gap-2">
          <div className={`rounded-lg px-3 py-2.5 border ${fiiNet >= 0 ? 'border-profit/25 bg-profit/5' : 'border-loss/25 bg-loss/5'}`}>
            <p className="text-muted text-[10px]">FII Net</p>
            <p className={`text-base font-bold tabular-nums ${fiiNet >= 0 ? 'text-profit' : 'text-loss'}`}>
              {fiiNet >= 0 ? '+' : ''}{inr(fiiNet, 0)}
            </p>
          </div>
          <div className={`rounded-lg px-3 py-2.5 border ${diiNet >= 0 ? 'border-profit/25 bg-profit/5' : 'border-loss/25 bg-loss/5'}`}>
            <p className="text-muted text-[10px]">DII Net</p>
            <p className={`text-base font-bold tabular-nums ${diiNet >= 0 ? 'text-profit' : 'text-loss'}`}>
              {diiNet >= 0 ? '+' : ''}{inr(diiNet, 0)}
            </p>
          </div>
        </div>
        {fii?.trend && (
          <p className="text-muted text-[10px] mt-2">Trend: <span className="text-slate-300 font-medium">{fii.trend}</span></p>
        )}
      </div>

      {/* VIX gauge */}
      <div className="mt-4 pt-4 border-t border-border">
        <div className="flex items-center justify-between">
          <span className="text-muted text-[10px] uppercase tracking-wide">India VIX</span>
          <span className={`text-sm font-bold tabular-nums ${vix < 15 ? 'text-profit' : vix > 20 ? 'text-loss' : 'text-warn'}`}>
            {inr(vix, 2)} <span className="text-[10px]">{vix < 15 ? 'calm' : vix > 20 ? 'fearful' : 'moderate'}</span>
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-white/5 mt-1.5 overflow-hidden">
          <div className="h-full rounded-full bg-gradient-to-r from-profit via-warn to-loss"
            style={{ width: `${Math.min(100, ((vix ?? 0) / 35) * 100)}%` }} />
        </div>
      </div>
    </Card>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   4 ── Top opportunities (unified scanner signals)
─────────────────────────────────────────────────────────────────────────── */
function ConvictionBar({ score }) {
  const v = Math.min(100, Math.abs(score ?? 0));
  const up = (score ?? 0) >= 0;
  return (
    <div className="flex items-center gap-2">
      <div className="w-14 h-1.5 rounded-full bg-white/5 overflow-hidden">
        <div className={`h-full rounded-full ${up ? 'bg-profit' : 'bg-loss'}`} style={{ width: `${v}%` }} />
      </div>
      <span className={`text-[11px] font-mono ${up ? 'text-profit' : 'text-loss'}`}>{Math.round(v)}%</span>
    </div>
  );
}

function SignalPill({ signal }) {
  const s = (signal || 'HOLD').toUpperCase();
  const isBuy = s.includes('BUY'); const isSell = s.includes('SELL');
  const cls = isBuy ? 'bg-profit/12 text-profit border-profit/25'
            : isSell ? 'bg-loss/12 text-loss border-loss/25'
            : 'bg-white/5 text-muted border-border';
  const label = s === 'STRONG_BUY' ? 'S.BUY' : s === 'STRONG_SELL' ? 'S.SELL' : s;
  return <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${cls}`}>{label}</span>;
}

function TopSignals({ shortlist, loading }) {
  const ranked = [...(shortlist || [])]
    .map(s => ({ ...s, conf: Math.min(100, Math.abs(s.master_score ?? 0)) }))
    .filter(s => /BUY|SELL/.test((s.signal || '').toUpperCase()))
    .sort((a, b) => b.conf - a.conf);
  const buyN  = ranked.filter(s => (s.signal || '').includes('BUY')).length;
  const sellN = ranked.filter(s => (s.signal || '').includes('SELL')).length;

  return (
    <Card className="flex flex-col h-full" pad="p-0">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <div className="flex items-center gap-2">
          <Zap size={14} className="text-cyan" />
          <h2 className="text-slate-100 text-sm font-semibold">Top Opportunities</h2>
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-profit/12 text-profit">{buyN} BUY</span>
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-loss/12 text-loss">{sellN} SELL</span>
        </div>
        <Link to="/discover/scanner" className="text-cyan text-xs hover:text-cyan/80 flex items-center gap-0.5">
          Scanner <ArrowRight size={12} />
        </Link>
      </div>

      {loading ? (
        <div className="py-12"><LoadingSpinner message="Loading signals…" /></div>
      ) : ranked.length === 0 ? (
        <div className="py-12 text-center text-muted text-sm">No actionable signals — run the market scanner</div>
      ) : (
        <div className="divide-y divide-border">
          {/* header row (desktop) */}
          <div className="hidden md:grid grid-cols-[1fr_72px_120px_70px_64px] gap-3 px-5 py-2 text-[10px] text-muted uppercase tracking-wide">
            <span>Symbol</span><span>Signal</span><span>Conviction</span><span>Vol</span><span>RSI</span>
          </div>
          {ranked.slice(0, 8).map((s) => (
            <Link key={s.symbol} to={`/s/${s.ticker}`}
              className={`grid grid-cols-2 md:grid-cols-[1fr_72px_120px_70px_64px] gap-2 md:gap-3 px-5 py-2.5 items-center hover:bg-white/[0.02] transition-colors ${
                (s.upper_circuit_days || 0) >= 3 ? 'bg-orange-500/[0.04]' : ''
              }`}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="w-7 h-7 rounded-lg grid place-items-center text-[11px] font-bold text-white shrink-0"
                  style={{ background: `hsl(${(s.ticker?.charCodeAt(0) || 65) * 41 % 360},45%,28%)` }}>
                  {s.ticker?.[0]}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-1 flex-wrap">
                    <p className="text-slate-200 text-sm font-semibold truncate">{s.ticker}</p>
                    {(s.upper_circuit_days || 0) >= 1 && (
                      <span
                        className={`text-[7px] font-bold px-1 rounded border leading-tight ${
                          (s.upper_circuit_days || 0) >= 3
                            ? 'text-orange-200 bg-orange-500/20 border-orange-400/40'
                            : 'text-amber-300 bg-amber-500/10 border-amber-500/30'
                        }`}
                        title={`${s.upper_circuit_days} day upper circuit streak`}
                      >
                        {s.upper_circuit_days >= 5 ? '▲▲▲' : s.upper_circuit_days >= 3 ? '▲▲' : '▲'} UC{s.upper_circuit_days}D
                      </span>
                    )}
                  </div>
                  {s.sector && <p className="text-muted text-[10px] truncate">{s.sector}</p>}
                </div>
              </div>
              <div className="md:block flex justify-end"><SignalPill signal={s.signal} /></div>
              <div className="hidden md:block"><ConvictionBar score={s.master_score} /></div>
              <div className="hidden md:block">
                <span className={`text-[11px] font-mono ${s.volume_ratio >= 2 ? 'text-cyan font-bold' : 'text-muted'}`}>
                  {inr(s.volume_ratio, 1)}×
                </span>
              </div>
              <div className="hidden md:block">
                <span className="text-[11px] font-mono text-slate-300">{s.rsi != null ? Math.round(s.rsi) : '—'}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   5 ── Volume movers (unusual volume surge)
─────────────────────────────────────────────────────────────────────────── */
function VolumeMovers({ shortlist }) {
  const movers = [...(shortlist || [])]
    .filter(s => (s.volume_ratio ?? 0) >= 1.2)
    .sort((a, b) => (b.volume_ratio ?? 0) - (a.volume_ratio ?? 0))
    .slice(0, 6);

  return (
    <Card className="flex flex-col h-full" pad="p-0">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
        <Flame size={14} className="text-warn" />
        <h2 className="text-slate-100 text-sm font-semibold">Volume Surge</h2>
        <span className="text-muted text-[10px] ml-auto">vs 20-day avg</span>
      </div>
      {movers.length === 0 ? (
        <div className="py-10 text-center text-muted text-xs">No unusual volume right now</div>
      ) : (
        <div className="divide-y divide-border">
          {movers.map((s) => (
            <Link key={s.symbol} to={`/s/${s.ticker}`}
              className="flex items-center justify-between px-5 py-2.5 hover:bg-white/[0.02] transition-colors">
              <div className="min-w-0">
                <p className="text-slate-200 text-sm font-semibold truncate">{s.ticker}</p>
                <p className="text-muted text-[10px]">{s.price_vs_ema20 != null ? `${s.price_vs_ema20 >= 0 ? '+' : ''}${inr(s.price_vs_ema20, 1)}% vs EMA20` : ''}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <SignalPill signal={s.signal} />
                <span className="text-cyan text-sm font-bold tabular-nums">{inr(s.volume_ratio, 1)}×</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
   Dashboard
─────────────────────────────────────────────────────────────────────────── */
export default function Dashboard() {
  const { portfolio, loading: pLoading } = usePortfolio();
  const shortlist = usePolledFetch('/api/v1/india/market-scanner/shortlist?limit=30', 30000, (d) => d?.shortlist ?? []);
  const slLoading = shortlist == null;

  if (pLoading) return <LoadingSpinner />;

  return (
    <div className="space-y-4 fade-in pb-6">
      {/* 1 — Index ticker strip */}
      <IndexStrip />

      {/* 2 — Portfolio hero + Market pulse */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <div className="xl:col-span-8"><PortfolioHero portfolio={portfolio} /></div>
        <div className="xl:col-span-4"><MarketPulse /></div>
      </div>

      {/* 3 — Top opportunities + Volume surge */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <div className="xl:col-span-8"><TopSignals shortlist={shortlist} loading={slLoading} /></div>
        <div className="xl:col-span-4"><VolumeMovers shortlist={shortlist} /></div>
      </div>

      {/* 4 — Sector performance */}
      <Card>
        <SectionTitle icon={Building2} action={
          <Link to="/sector-heatmap" className="text-cyan text-xs hover:text-cyan/80 flex items-center gap-0.5">
            Heatmap <ArrowRight size={12} />
          </Link>
        }>Sector Performance</SectionTitle>
        <SectorHeatmapWidget compact={true} maxSectors={8} />
      </Card>

      {/* 5 — Upcoming events */}
      <UpcomingEventsWidget maxItems={5} compact={false} />
    </div>
  );
}
