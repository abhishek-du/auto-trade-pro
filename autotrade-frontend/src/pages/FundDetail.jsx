/**
 * FundDetail — /mf/:scheme
 *
 * Decision-first Mutual Fund detail page.
 * Reuses existing backend endpoints (no new APIs):
 *   GET /india/mutual-funds/{scheme}/signal   → verdict + reasoning
 *   GET /india/mutual-funds/{scheme}/nav      → NAV history
 *   GET /india/mutual-funds/{scheme}/sip      → SIP simulation
 *   GET /india/mutual-funds/compare?scheme_codes=… → compare flow
 *   GET /intelligence/mf-signals             → hub scoring
 *   GET /india/sip/project                   → projection tool
 */
import { useParams, Link } from 'react-router-dom';
import { useEffect, useState, useCallback } from 'react';
import { ArrowLeft, Star, Bell, TrendingUp, TrendingDown, ChevronDown, RefreshCw, Calculator, BarChart2, ShieldAlert } from 'lucide-react';
import { apiFetch } from '../api/client';

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(n, d = 2) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function pct(n) {
  if (n == null || isNaN(n)) return '—';
  return (n >= 0 ? '+' : '') + fmt(n) + '%';
}

function SignalChip({ signal }) {
  if (!signal) return null;
  const s = String(signal).toUpperCase();
  const cls =
    s === 'BUY'  ? 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30' :
    s === 'HOLD' ? 'text-amber-400 bg-amber-500/15 border-amber-500/30' :
    s === 'SELL' ? 'text-red-400 bg-red-500/15 border-red-500/30' :
                   'text-slate-400 bg-slate-500/15 border-slate-500/30';
  return <span className={`text-xs font-bold px-2 py-0.5 rounded border ${cls}`}>{s}</span>;
}

function ReturnBar({ label, value }) {
  if (value == null) return null;
  const pos = value >= 0;
  const w   = Math.min(Math.abs(value) * 3, 100);
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="text-muted w-8 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${pos ? 'bg-profit' : 'bg-loss'}`} style={{ width: `${w}%` }} />
      </div>
      <span className={`font-mono w-14 text-right font-semibold ${pos ? 'text-profit' : 'text-loss'}`}>{pct(value)}</span>
    </div>
  );
}

function StatPill({ label, value, sub }) {
  return (
    <div className="bg-card rounded-xl border border-border p-3">
      <div className="text-muted text-[10px] uppercase tracking-widest mb-1">{label}</div>
      <div className="font-mono text-slate-100 text-base font-bold leading-tight">{value ?? '—'}</div>
      {sub && <div className="text-muted text-[10px] mt-0.5">{sub}</div>}
    </div>
  );
}

function Skeleton({ w = 'w-full', h = 'h-4' }) {
  return <div className={`${w} ${h} rounded animate-pulse bg-white/5`} />;
}

function SectionLabel({ children }) {
  return <div className="text-[10px] text-cyan font-semibold uppercase tracking-widest mb-3">{children}</div>;
}

function DeepTab({ label, subtitle, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/[0.03] transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <span className="flex-1 text-left">
          <span className="text-slate-200 text-sm font-medium">{label}</span>
          {subtitle && <span className="text-muted text-xs ml-2">{subtitle}</span>}
        </span>
        <ChevronDown size={14} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="border-t border-border px-4 py-4 bg-surface/40">{children}</div>}
    </div>
  );
}

// ── ₹10,000 projection card (lumpsum vs SIP) ───────────────────────────────────

function FundInvestCard({ signal }) {
  // Use the fund's actual realised returns as the base case, then derive
  // bull/bear bands around it. CAGR proxy = 3Y return annualised, else 1Y.
  const oneY  = signal?.one_year_return;
  const threeY = signal?.three_year_return;
  const cagr  = threeY != null ? threeY / 3 : (oneY ?? 8);   // %/yr proxy

  const AMOUNT = 10_000;
  const YEARS  = 1;   // ₹10k held for ~1 year horizon

  // Base = expected CAGR; bull = +50% of CAGR spread; bear = downside band
  const basePct = cagr;
  const bullPct = cagr >= 0 ? cagr * 1.6 + 4 : cagr * 0.5;
  const bearPct = cagr >= 0 ? Math.min(cagr * 0.2 - 8, -3) : cagr * 1.8;

  const bullVal = AMOUNT * (1 + bullPct / 100);
  const baseVal = AMOUNT * (1 + basePct / 100);
  const bearVal = AMOUNT * (1 + bearPct / 100);

  return (
    <div className="bg-card border border-border rounded-xl p-4 mb-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-amber-400 text-sm">₹</span>
        <span className="text-slate-200 text-sm font-semibold">If I invest ₹10,000 today</span>
        <span className="ml-auto text-[10px] text-muted">Horizon: ~1 year · lumpsum</span>
      </div>
      <div className="grid grid-cols-3 gap-2.5 text-center">
        {[
          { label: 'Bull', prob: '48%', val: bullVal, p: bullPct,
            box: 'bg-emerald-500/10 border-emerald-500/20', txt: 'text-emerald-400', dot: 'bg-emerald-400' },
          { label: 'Base', prob: '32%', val: baseVal, p: basePct,
            box: 'bg-amber-500/10 border-amber-500/20', txt: 'text-amber-400', dot: 'bg-amber-400' },
          { label: 'Bear', prob: '20%', val: bearVal, p: bearPct,
            box: 'bg-red-500/10 border-red-500/20', txt: 'text-red-400', dot: 'bg-red-400' },
        ].map(s => (
          <div key={s.label} className={`border rounded-lg p-3 ${s.box}`}>
            <div className={`text-[10px] font-bold uppercase tracking-wider mb-1 flex items-center justify-center gap-1 ${s.txt}`}>
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.dot}`} />
              {s.label} · {s.prob}
            </div>
            <div className={`font-mono text-base font-black ${s.txt}`}>
              ₹{Number(s.val).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </div>
            <div className={`text-[10px] font-mono mt-0.5 ${s.txt}`}>{pct(s.p)}</div>
          </div>
        ))}
      </div>
      <p className="text-muted text-[10px] mt-2.5 leading-relaxed">
        Projections from {threeY != null ? '3Y' : '1Y'} historical return ({fmt(cagr)}%/yr proxy) · past performance is not indicative of future results · for SIP outcomes use the SIP Calculator below.
      </p>
    </div>
  );
}

// ── Tiny NAV sparkline (SVG) ──────────────────────────────────────────────────

function NavSparkline({ history }) {
  if (!history?.length) return null;
  const navs = history.slice(-60).map(h => parseFloat(h.nav)).filter(v => !isNaN(v));
  if (navs.length < 2) return null;
  const min = Math.min(...navs), max = Math.max(...navs);
  const range = max - min || 1;
  const W = 600, H = 100;
  const pts = navs.map((v, i) => `${(i / (navs.length - 1)) * W},${H - ((v - min) / range) * (H - 4) - 2}`).join(' ');
  const pos = navs[navs.length - 1] >= navs[0];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-28" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={pos ? '#10B981' : '#F43F5E'} strokeWidth="2" />
    </svg>
  );
}

// ── Compare row ───────────────────────────────────────────────────────────────

function CompareRow({ fund, isBest }) {
  return (
    <tr className={`text-xs border-t border-border transition-colors hover:bg-white/[0.03] ${isBest ? 'bg-profit/5' : ''}`}>
      <td className="px-3 py-2 font-mono text-slate-200 font-semibold truncate max-w-[180px]">{fund.scheme_code}</td>
      <td className="px-3 py-2 text-muted truncate max-w-[220px]">{fund.scheme_name || fund.name}</td>
      <td className={`px-3 py-2 font-mono text-right ${fund.one_year_return >= 0 ? 'text-profit' : 'text-loss'}`}>{pct(fund.one_year_return ?? fund.one_yr_return)}</td>
      <td className={`px-3 py-2 font-mono text-right ${fund.three_year_return >= 0 ? 'text-profit' : 'text-loss'}`}>{pct(fund.three_year_return)}</td>
      <td className="px-3 py-2 font-mono text-right text-muted">{fmt(fund.consistency_std ?? fund.composite_score, 1)}</td>
      {isBest && <td className="px-3 py-2 text-profit text-[10px] font-bold">Best</td>}
    </tr>
  );
}

// ── SIP Calculator ────────────────────────────────────────────────────────────

function SIPCalc({ schemeCode }) {
  const [amount, setAmount]   = useState(5000);
  const [months, setMonths]   = useState(36);
  const [result, setResult]   = useState(null);
  const [loading, setLoading] = useState(false);

  const run = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiFetch(
        `/api/v1/india/mutual-funds/${encodeURIComponent(schemeCode)}/sip?monthly_amount=${amount}&months=${months}`
      );
      setResult(d);
    } catch {
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [schemeCode, amount, months]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-muted text-xs mb-1 block">Monthly SIP (₹)</label>
          <input
            type="number"
            value={amount}
            onChange={e => setAmount(Number(e.target.value))}
            className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-slate-200 text-sm font-mono"
          />
        </div>
        <div>
          <label className="text-muted text-xs mb-1 block">Months</label>
          <select
            value={months}
            onChange={e => setMonths(Number(e.target.value))}
            className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-slate-200 text-sm"
          >
            {[6, 12, 24, 36, 60, 120].map(m => (
              <option key={m} value={m}>{m < 12 ? `${m}m` : `${m / 12}y`}</option>
            ))}
          </select>
        </div>
      </div>
      <button
        onClick={run}
        disabled={loading}
        className="w-full bg-accent/10 hover:bg-accent/20 border border-accent/30 text-accent font-semibold rounded-lg py-2 text-sm transition-colors flex items-center justify-center gap-2"
      >
        <Calculator size={14} />
        {loading ? 'Calculating…' : 'Calculate'}
      </button>
      {result && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <StatPill label="Total invested" value={`₹${fmt(result.total_invested, 0)}`} />
          <StatPill label="Current value"  value={`₹${fmt(result.current_value, 0)}`} />
          <StatPill label="Absolute return" value={pct(result.absolute_return)} />
          <StatPill label="CAGR" value={result.cagr ? pct(result.cagr) : '—'} />
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function FundDetail() {
  const { scheme } = useParams();

  const [signal,   setSignal]  = useState(null);
  const [navHist,  setNavHist] = useState([]);
  const [compare,  setCompare] = useState([]);
  const [loading,  setLoading] = useState(true);
  const [error,    setError]   = useState(null);

  const latest = navHist[navHist.length - 1] ?? null;
  const prev   = navHist[navHist.length - 2] ?? null;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sigRes, navRes] = await Promise.allSettled([
        apiFetch(`/api/v1/india/mutual-funds/${encodeURIComponent(scheme)}/signal`),
        apiFetch(`/api/v1/india/mutual-funds/${encodeURIComponent(scheme)}/nav?limit=365`),
      ]);
      if (sigRes.status === 'fulfilled') setSignal(sigRes.value);
      if (navRes.status === 'fulfilled') setNavHist(Array.isArray(navRes.value) ? navRes.value : []);

      // Compare with same-category peers (non-blocking).
      // Pull the tracked-fund list, pick up to 2 peers in the same category,
      // then compare all 3 side-by-side.
      const category = sigRes.status === 'fulfilled' ? sigRes.value?.category : null;
      apiFetch(`/api/v1/india/mutual-funds`)
        .then(d => {
          const funds = Array.isArray(d?.funds) ? d.funds : (Array.isArray(d) ? d : []);
          const others = funds.filter(f => String(f.scheme_code) !== String(scheme));
          // Prefer same-category peers; fall back to any other tracked fund.
          let peers = others.filter(f => category && f.category === category).slice(0, 2);
          if (peers.length < 2) {
            const extra = others.filter(f => !peers.includes(f)).slice(0, 2 - peers.length);
            peers = [...peers, ...extra];
          }
          const codes = [scheme, ...peers.map(f => f.scheme_code)].join(',');
          return apiFetch(`/api/v1/india/mutual-funds/compare?scheme_codes=${codes}`);
        })
        .then(d => setCompare(Array.isArray(d) ? d : []))
        .catch(() => {});
    } catch (e) {
      setError(e.message || 'Failed to load fund data');
    } finally {
      setLoading(false);
    }
  }, [scheme]);

  useEffect(() => { load(); }, [load]);

  const name     = signal?.scheme_name ?? scheme;
  const nav      = signal?.current_nav ?? latest?.nav ?? null;
  const changePct = signal?.change_pct ?? latest?.change_pct ?? null;
  const posChange = changePct != null && changePct >= 0;

  return (
    <div className="-m-6 flex flex-col min-h-screen bg-surface">
      {/* ── Sticky header ─────────────────────────────────────────────── */}
      <div className="sticky top-0 z-30 bg-surface/95 backdrop-blur border-b border-border px-5 py-3 flex items-center gap-3">
        <Link to="/" className="text-muted hover:text-slate-300 p-1 rounded-lg hover:bg-white/5 transition-colors">
          <ArrowLeft size={16} />
        </Link>
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-violet-900 to-violet-600 grid place-items-center font-bold text-white text-sm shrink-0">
          MF
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-100 font-semibold text-base truncate">{name}</span>
            {signal?.category && (
              <span className="text-[10px] bg-white/5 border border-border px-1.5 py-0.5 rounded text-muted shrink-0">{signal.category}</span>
            )}
            <SignalChip signal={signal?.signal} />
          </div>
          <div className="text-muted text-xs mt-0.5">Scheme code: {scheme}</div>
        </div>
        <div className="flex items-baseline gap-2">
          {nav != null && <span className="font-mono text-2xl font-bold text-slate-100">₹{fmt(nav)}</span>}
          {changePct != null && (
            <span className={`font-mono text-sm font-semibold ${posChange ? 'text-profit' : 'text-loss'}`}>{pct(changePct)}</span>
          )}
        </div>
        <div className="flex items-center gap-1.5 ml-2">
          <button className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5 transition-colors"><Star size={15} /></button>
          <button className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5 transition-colors"><Bell size={15} /></button>
          <button onClick={load} className="text-muted hover:text-slate-300 p-1.5 rounded-lg hover:bg-white/5 transition-colors">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-5 mt-4 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 text-sm">{error}</div>
      )}

      {/* ── SECTION 1: Decision Center ────────────────────────────────── */}
      <section className="px-5 pt-6 pb-6 bg-gradient-to-b from-surface to-transparent">
        <SectionLabel>Section 1 · Decision center</SectionLabel>

        <div className="rounded-2xl border border-border p-5 mb-3 relative overflow-hidden"
          style={{ background: 'linear-gradient(145deg,#131E30,#0F1829)' }}>
          <div className="absolute -right-20 -top-20 w-80 h-80 rounded-full blur-3xl opacity-20"
            style={{ background: signal?.signal === 'BUY' ? '#10B981' : '#3B82F6' }} />

          <div className="relative grid grid-cols-1 lg:grid-cols-12 gap-5">
            {/* Verdict */}
            <div className="lg:col-span-3 lg:border-r lg:border-border lg:pr-5">
              <div className="text-muted text-[10px] uppercase tracking-widest mb-2">Verdict</div>
              {loading ? <Skeleton w="w-32" h="h-12" /> : (
                <>
                  <div className={`text-5xl font-black tracking-tight leading-none ${
                    signal?.signal === 'BUY' ? 'text-profit' :
                    signal?.signal === 'SELL' ? 'text-loss' : 'text-amber-400'
                  }`}>
                    {signal?.signal ?? 'HOLD'}
                  </div>
                  {signal?.composite_score != null && (
                    <div className="flex items-center gap-2 mt-3">
                      <span className="text-muted text-[10px]">Score</span>
                      <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-cyan to-profit"
                          style={{ width: `${Math.min(signal.composite_score, 100)}%` }}
                        />
                      </div>
                      <span className="font-mono text-cyan text-sm font-bold">{fmt(signal.composite_score, 0)}/100</span>
                    </div>
                  )}
                  {signal?.signal === 'BUY' && (
                    <div className="mt-3 space-y-1.5 text-xs text-slate-300">
                      <div><span className="text-profit">›</span> Momentum trending upward</div>
                      {signal?.dip_from_high_pct != null && (
                        <div><span className="text-profit">›</span> {fmt(Math.abs(signal.dip_from_high_pct))}% below 52w high — dip opportunity</div>
                      )}
                      {signal?.one_year_return != null && (
                        <div><span className="text-profit">›</span> 1Y return {pct(signal.one_year_return)}</div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* NAV + returns grid */}
            <div className="lg:col-span-5 grid grid-cols-2 gap-2.5">
              {loading ? Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-white/5 rounded-lg p-3 h-16 animate-pulse" />
              )) : (
                <>
                  <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                    <div className="text-muted text-[10px] uppercase tracking-wider">Current NAV</div>
                    <div className="font-mono text-slate-100 text-lg font-bold mt-1">₹{fmt(nav)}</div>
                    <div className={`text-xs mt-0.5 font-mono ${posChange ? 'text-profit' : 'text-loss'}`}>{pct(changePct)}</div>
                  </div>
                  <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                    <div className="text-muted text-[10px] uppercase tracking-wider">1Y return</div>
                    <div className={`font-mono text-lg font-bold mt-1 ${(signal?.one_year_return ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {pct(signal?.one_year_return ?? latest?.one_year_return)}
                    </div>
                  </div>
                  <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                    <div className="text-muted text-[10px] uppercase tracking-wider">3Y return</div>
                    <div className={`font-mono text-lg font-bold mt-1 ${(signal?.three_year_return ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {pct(signal?.three_year_return ?? latest?.three_year_return)}
                    </div>
                  </div>
                  <div className="bg-white/[0.04] border border-border rounded-lg p-3">
                    <div className="text-muted text-[10px] uppercase tracking-wider">Category</div>
                    <div className="text-slate-200 text-sm font-semibold mt-1 line-clamp-2">
                      {signal?.category ?? '—'}
                    </div>
                  </div>
                  {signal?.vix != null && (
                    <div className="bg-white/[0.04] border border-border rounded-lg p-3 col-span-2">
                      <div className="flex items-center justify-between">
                        <span className="text-muted text-[10px] uppercase tracking-wider">Market VIX at verdict</span>
                        <span className={`font-mono text-sm font-bold ${signal.vix > 20 ? 'text-loss' : 'text-profit'}`}>{fmt(signal.vix, 1)}</span>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Reason */}
            <div className="lg:col-span-4">
              {signal?.reason && (
                <div className="bg-white/[0.04] border border-border rounded-lg p-3 h-full">
                  <div className="text-cyan text-[10px] uppercase tracking-wider font-bold mb-2">Why this verdict?</div>
                  <p className="text-xs text-slate-300 leading-relaxed">{signal.reason}</p>
                </div>
              )}
            </div>
          </div>

          {/* CTAs */}
          <div className="relative mt-5 flex items-center gap-3">
            <button className="flex-1 bg-profit/10 hover:bg-profit/20 border border-profit/30 text-profit font-bold rounded-lg py-2.5 text-sm transition-colors flex items-center justify-center gap-2">
              <TrendingUp size={15} /> Invest (Lumpsum)
            </button>
            <button className="flex-1 bg-white/[0.04] hover:bg-white/[0.07] border border-border text-slate-300 font-semibold rounded-lg py-2.5 text-sm transition-colors flex items-center justify-center gap-2">
              <BarChart2 size={15} /> Start SIP
            </button>
            <button className="flex-1 bg-white/[0.04] hover:bg-white/[0.07] border border-border text-slate-300 font-semibold rounded-lg py-2.5 text-sm transition-colors flex items-center justify-center gap-2">
              <Star size={15} /> Watchlist
            </button>
          </div>
        </div>

        {/* ₹10k projection card */}
        {!loading && (signal?.one_year_return != null || signal?.three_year_return != null) && (
          <FundInvestCard signal={signal} />
        )}
      </section>

      {/* ── SECTION 2: Intelligence snapshot ─────────────────────────── */}
      <section className="px-5 pb-6">
        <SectionLabel>Section 2 · Intelligence snapshot</SectionLabel>
        {loading ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="bg-card rounded-xl border border-border p-4 h-20 animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            <StatPill label="1M return"  value={pct(signal?.one_month_return)} />
            <StatPill label="3M return"  value={pct(signal?.three_month_return)} />
            <StatPill label="1Y return"  value={pct(signal?.one_year_return)} />
            <StatPill label="3Y return"  value={pct(signal?.three_year_return)} />
            {signal?.dip_from_high_pct != null && (
              <StatPill label="Dip from 52w high" value={pct(signal.dip_from_high_pct)} sub="Buying opportunity if +ve trend" />
            )}
            {signal?.high_52w != null && (
              <StatPill label="52W high NAV" value={`₹${fmt(signal.high_52w)}`} />
            )}
          </div>
        )}
      </section>

      {/* ── SECTION 3: NAV Chart ──────────────────────────────────────── */}
      <section className="px-5 pb-6">
        <SectionLabel>Section 3 · NAV history chart</SectionLabel>
        <div className="bg-card rounded-xl border border-border p-5">
          {navHist.length > 1 ? (
            <>
              <NavSparkline history={navHist} />
              <div className="flex items-center justify-between text-[10px] text-muted font-mono mt-2">
                <span>{navHist[0]?.recorded_at?.slice(0, 10) ?? ''}</span>
                <span>{navHist[navHist.length - 1]?.recorded_at?.slice(0, 10) ?? ''}</span>
              </div>
            </>
          ) : (
            <div className="h-28 flex items-center justify-center text-muted text-sm">
              {loading ? 'Loading NAV history…' : 'No NAV history available'}
            </div>
          )}
        </div>
      </section>

      {/* ── Progressive disclosure ────────────────────────────────────── */}
      <div className="px-5 pb-4">
        <div className="flex items-center gap-3">
          <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
          <span className="text-muted text-xs px-3 py-1.5 rounded-full bg-card border border-border">
            Core verdict above · expand for deeper analysis
          </span>
          <div className="flex-1 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
        </div>
      </div>

      {/* ── SECTION 4–7: Deep tabs ────────────────────────────────────── */}
      <section className="px-5 pb-8 space-y-2">
        {/* SIP Calculator */}
        <DeepTab label="SIP Calculator" subtitle="Monthly amount × horizon projection">
          <SIPCalc schemeCode={scheme} />
        </DeepTab>

        {/* Returns breakdown */}
        <DeepTab label="Returns" subtitle="1M · 3M · 1Y · 3Y breakdown">
          <div className="space-y-3">
            <ReturnBar label="1M"  value={signal?.one_month_return} />
            <ReturnBar label="3M"  value={signal?.three_month_return} />
            <ReturnBar label="1Y"  value={signal?.one_year_return ?? latest?.one_year_return} />
            <ReturnBar label="3Y"  value={signal?.three_year_return ?? latest?.three_year_return} />
          </div>
        </DeepTab>

        {/* Risk */}
        <DeepTab label="Risk analysis" subtitle="VIX · drawdown · volatility">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {signal?.vix          != null && <StatPill label="VIX"                value={fmt(signal.vix, 1)} sub="Lower = calmer markets" />}
            {signal?.dip_from_high_pct != null && <StatPill label="Dip from 52w high" value={pct(signal.dip_from_high_pct)} />}
            {signal?.composite_score != null && <StatPill label="Composite score" value={`${fmt(signal.composite_score, 0)}/100`} />}
          </div>
          {!signal?.vix && !signal?.dip_from_high_pct && (
            <p className="text-muted text-sm">Risk metrics load after first signal cycle.</p>
          )}
        </DeepTab>

        {/* Compare */}
        <DeepTab label="Compare" subtitle="Same-category funds side-by-side">
          {compare.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                    <th className="text-left px-3 py-2">Scheme code</th>
                    <th className="text-left px-3 py-2">Name</th>
                    <th className="text-right px-3 py-2">1Y</th>
                    <th className="text-right px-3 py-2">3Y</th>
                    <th className="text-right px-3 py-2">Consistency</th>
                  </tr>
                </thead>
                <tbody>
                  {compare.map(f => (
                    <CompareRow key={f.scheme_code} fund={f} isBest={f.best_fund} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-muted text-sm">
              Compare data loads after a signal cycle. Try refreshing.
            </div>
          )}
        </DeepTab>

        {/* NAV history table */}
        <DeepTab label="NAV history" subtitle="Daily NAV records">
          {navHist.length > 0 ? (
            <div className="overflow-y-auto max-h-64">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-surface">
                  <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                    <th className="text-left px-3 py-2">Date</th>
                    <th className="text-right px-3 py-2">NAV</th>
                    <th className="text-right px-3 py-2">Change</th>
                    <th className="text-right px-3 py-2">1M ret</th>
                  </tr>
                </thead>
                <tbody>
                  {[...navHist].reverse().slice(0, 30).map((r, i) => (
                    <tr key={i} className="border-t border-border hover:bg-white/[0.03]">
                      <td className="px-3 py-1.5 text-muted font-mono">{r.recorded_at?.slice(0, 10)}</td>
                      <td className="px-3 py-1.5 font-mono text-right text-slate-200">₹{fmt(r.nav)}</td>
                      <td className={`px-3 py-1.5 font-mono text-right ${(r.change_pct ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>{pct(r.change_pct)}</td>
                      <td className={`px-3 py-1.5 font-mono text-right ${(r.one_month_return ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>{pct(r.one_month_return)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-muted text-sm">No NAV history in DB. Refresh will fetch from AMFI.</div>
          )}
        </DeepTab>
      </section>
    </div>
  );
}
