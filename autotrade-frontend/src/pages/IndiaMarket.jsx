import { useState, useEffect, useCallback } from 'react';
import {
  Activity, Globe, Zap, BarChart2, TrendingUp,
} from 'lucide-react';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Cell,
} from 'recharts';
import MetricCard     from '../components/MetricCard';
import SignalBadge    from '../components/SignalBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import {
  getIndiaMarketStatus, getIndiaVix, getIndiaFiiDii,
  getIndiaSignals, getIndiaSectorPerf, getIndiaOptionsChain,
} from '../api/client';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtIdx(n) {
  if (n == null) return '—';
  return (+n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function pcrLabel(pcr) {
  if (pcr == null) return '—';
  if (pcr > 1.5)  return 'Strong Bullish Sentiment';
  if (pcr > 1.2)  return 'Moderately Bullish';
  if (pcr > 0.8)  return 'Neutral';
  if (pcr > 0.5)  return 'Moderately Bearish';
  return 'Strong Bearish Sentiment';
}

function SectorBox({ sector }) {
  const ret = sector.return_30d ?? 0;
  let bg, col;
  if      (ret >=  3) { bg = 'rgba(16,185,129,0.22)'; col = '#10B981'; }
  else if (ret >=  1) { bg = 'rgba(16,185,129,0.10)'; col = '#34D399'; }
  else if (ret >=  0) { bg = 'rgba(16,185,129,0.04)'; col = '#6EE7B7'; }
  else if (ret >= -1) { bg = 'rgba(244,63,94,0.04)';  col = '#FDA4AF'; }
  else if (ret >= -3) { bg = 'rgba(244,63,94,0.10)';  col = '#F87171'; }
  else                { bg = 'rgba(244,63,94,0.22)';  col = '#F43F5E'; }

  return (
    <div className="rounded-lg p-3 border flex flex-col gap-1"
      style={{ background: bg, borderColor: col + '50' }}>
      <p className="text-[10px] font-bold uppercase tracking-wider truncate" style={{ color: col }}>
        {sector.name}
      </p>
      <p className="text-base font-extrabold tabular-nums leading-none" style={{ color: col }}>
        {ret >= 0 ? '+' : ''}{ret?.toFixed(1) ?? '—'}%
      </p>
      <p className="text-[10px] font-medium" style={{ color: col + 'aa' }}>
        {sector.signal}
      </p>
    </div>
  );
}

const CHART_STYLE = {
  contentStyle: { background: '#0F1829', border: '1px solid #1E2D45', borderRadius: 8, fontSize: 11 },
  labelStyle:   { color: '#94A3B8' },
};

// ── Page ─────────────────────────────────────────────────────────────────────

export default function IndiaMarket() {
  const [marketStatus, setMarketStatus] = useState(null);
  const [vixData,      setVixData]      = useState(null);
  const [fiiDii,       setFiiDii]       = useState(null);
  const [signals,      setSignals]      = useState([]);
  const [sectors,      setSectors]      = useState([]);
  const [optionsNifty, setOptionsNifty] = useState(null);
  const [sigTab,       setSigTab]       = useState('stocks');
  const [loading,      setLoading]      = useState(true);

  const loadAll = useCallback(async () => {
    const [ms, vx, fd, sec, opts] = await Promise.allSettled([
      getIndiaMarketStatus(),
      getIndiaVix(),
      getIndiaFiiDii(),
      getIndiaSectorPerf(),
      getIndiaOptionsChain('NIFTY'),
    ]);
    if (ms.status  === 'fulfilled') setMarketStatus(ms.value);
    if (vx.status  === 'fulfilled') setVixData(vx.value);
    if (fd.status  === 'fulfilled') setFiiDii(fd.value);
    if (sec.status === 'fulfilled') setSectors(sec.value?.sectors ?? []);
    if (opts.status === 'fulfilled') setOptionsNifty(opts.value);
    setLoading(false);
  }, []);

  const loadSignals = useCallback(async (cat) => {
    try {
      const data = await getIndiaSignals(cat);
      setSignals(Array.isArray(data) ? data : data?.signals ?? []);
    } catch {}
  }, []);

  useEffect(() => {
    loadAll();
    const id = setInterval(loadAll, 30000);
    return () => clearInterval(id);
  }, [loadAll]);

  useEffect(() => {
    loadSignals(sigTab);
    const id = setInterval(() => loadSignals(sigTab), 15000);
    return () => clearInterval(id);
  }, [loadSignals, sigTab]);

  if (loading) return <LoadingSpinner />;

  const nifty     = marketStatus?.nifty;
  const bankNifty = marketStatus?.bank_nifty;
  const sensex    = marketStatus?.sensex;
  const nseOpen   = marketStatus?.nse_open ?? false;
  const istTime   = (marketStatus?.ist_time ?? '').split(' ')[1] ?? '—';
  const holiday   = marketStatus?.holiday_name ?? '';

  const chartData = (fiiDii?.chart_data ?? []).slice(-30).map(d => ({
    date: (d.date ?? '').slice(5),
    fii:  d.fii_net,
    dii:  d.dii_net,
  }));

  const todayFii   = fiiDii?.today?.fii_net ?? null;
  const todayDii   = fiiDii?.today?.dii_net ?? null;
  const trend      = fiiDii?.trend ?? 'MIXED';

  const pcr      = optionsNifty?.pcr;
  const maxPain  = optionsNifty?.max_pain;
  const spot     = optionsNifty?.spot_price;
  const support  = optionsNifty?.support_levels ?? [];
  const resist   = optionsNifty?.resistance_levels ?? [];
  const mpDiff   = spot != null && maxPain != null ? spot - maxPain : null;

  return (
    <div className="space-y-5 fade-in">

      {/* ── Top status bar ─────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border px-5 py-3 flex items-center justify-between"
        style={{ background: '#0F1829' }}>
        <div className="flex items-center gap-3">
          <Globe size={15} className="text-cyan shrink-0" />
          <span className="text-slate-200 font-bold text-sm">Indian Market Dashboard</span>
          {holiday && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-warn/10 text-warn border border-warn/20">
              {holiday}
            </span>
          )}
        </div>
        <div className="flex items-center gap-5">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full shrink-0 ${nseOpen ? 'bg-profit' : 'bg-loss'}`} />
            <span className="text-sm font-semibold text-slate-200">
              NSE: {nseOpen ? 'OPEN' : 'CLOSED'}
            </span>
          </div>
          <span className="text-muted font-mono text-sm">IST: {istTime}</span>
        </div>
      </div>

      {/* ── Row 1 — 4 index cards ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <MetricCard title="NIFTY 50"    value={fmtIdx(nifty?.price)}
          subtitle="NSE Large Cap Index" trend={nifty?.change_pct} icon={TrendingUp} />
        <MetricCard title="BANK NIFTY"  value={fmtIdx(bankNifty?.price)}
          subtitle="Banking Index" trend={bankNifty?.change_pct} icon={BarChart2} />
        <MetricCard title="INDIA VIX"
          value={vixData?.vix?.toFixed(2) ?? marketStatus?.india_vix?.toFixed(2) ?? '—'}
          subtitle={vixData?.label ?? 'Volatility Index'} trend={null} icon={Activity} />
        <MetricCard title="SENSEX"      value={fmtIdx(sensex?.price)}
          subtitle="BSE 30 Index" trend={sensex?.change_pct} icon={TrendingUp} />
      </div>

      {/* ── Row 2 — FII/DII chart + Signals ────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">

        {/* FII/DII bar chart — 60% */}
        <div className="xl:col-span-3 rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
          <div className="mb-4">
            <h2 className="text-slate-100 font-semibold text-sm">FII / DII Flow — Last 30 Days</h2>
            <p className="text-muted text-xs mt-0.5">
              Trend:{' '}
              <span className={`font-bold ${
                trend === 'ACCUMULATION' ? 'text-profit' :
                trend === 'DISTRIBUTION' ? 'text-loss' : 'text-warn'
              }`}>{trend}</span>
            </p>
          </div>

          {chartData.length === 0 ? (
            <div className="flex items-center justify-center h-48 text-muted text-sm">
              No FII/DII data — run seed to populate
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2D45" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#4E6280', fontSize: 10 }}
                  tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: '#4E6280', fontSize: 10 }}
                  tickLine={false} axisLine={false} />
                <Tooltip
                  {...CHART_STYLE}
                  formatter={(v, name) => [
                    `${v >= 0 ? '+' : ''}${(+v).toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`,
                    name === 'fii' ? 'FII Net' : 'DII Net',
                  ]}
                />
                <ReferenceLine y={0} stroke="#1E2D45" />
                <Bar dataKey="fii" name="fii" radius={[2, 2, 0, 0]}>
                  {chartData.map((entry, idx) => (
                    <Cell key={idx} fill={entry.fii >= 0 ? '#10B981' : '#F43F5E'} />
                  ))}
                </Bar>
                <Line type="monotone" dataKey="dii" name="dii"
                  stroke="#3B82F6" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          )}

          {(todayFii != null || todayDii != null) && (
            <p className="text-xs text-muted mt-3 border-t border-border pt-3">
              <span className={`font-semibold ${todayFii >= 0 ? 'text-profit' : 'text-loss'}`}>
                FII {todayFii >= 0 ? 'bought' : 'sold'}{' '}
                {Math.abs(todayFii ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr
              </span>
              {' | '}
              <span className={`font-semibold ${todayDii >= 0 ? 'text-profit' : 'text-loss'}`}>
                DII {todayDii >= 0 ? 'bought' : 'sold'}{' '}
                {Math.abs(todayDii ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr
              </span>
              {' '}today
            </p>
          )}
        </div>

        {/* Signals panel — 40% */}
        <div className="xl:col-span-2 rounded-xl border border-border flex flex-col overflow-hidden"
          style={{ background: '#0F1829' }}>
          <div className="px-4 pt-4 pb-3 border-b border-border">
            <div className="flex items-center gap-2 mb-3">
              <Zap size={13} className="text-cyan" />
              <h2 className="text-slate-100 font-semibold text-sm">India Signals</h2>
            </div>
            <div className="flex gap-1">
              {['stocks', 'indices', 'forex'].map(tab => (
                <button key={tab} onClick={() => setSigTab(tab)}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold capitalize transition-colors ${
                    sigTab === tab
                      ? 'bg-accent/20 text-accent border border-accent/30'
                      : 'text-muted hover:text-slate-300 hover:bg-white/5'
                  }`}>
                  {tab}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-1 divide-y divide-border">
            {signals.length === 0 ? (
              <div className="flex items-center justify-center h-32">
                <p className="text-muted text-sm">No signals</p>
              </div>
            ) : (
              signals.slice(0, 14).map((s, i) => (
                <div key={i} className="flex items-center justify-between py-2.5">
                  <div>
                    <p className="text-slate-200 text-sm font-semibold">
                      {s.symbol?.replace('.NS', '')}
                    </p>
                    {s.confidence != null && (
                      <p className="text-muted text-[10px] mt-0.5">
                        {s.pattern_name ?? s.timeframe} · {(+s.confidence).toFixed(1)}%
                      </p>
                    )}
                  </div>
                  <SignalBadge signal={s} />
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── Row 3 — Sector Heatmap ─────────────────────────────────────────── */}
      <div className="rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
        <h2 className="text-slate-100 font-semibold text-sm mb-4">
          Sector Performance — 30 Day
        </h2>
        {sectors.length === 0 ? (
          <p className="text-muted text-sm">No sector data — run seed to populate candle history.</p>
        ) : (
          <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-2">
            {sectors.map(s => <SectorBox key={s.name} sector={s} />)}
          </div>
        )}
      </div>

      {/* ── Row 4 — PCR + Max Pain ─────────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">

        {/* PCR panel */}
        <div className="rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
          <div className="flex items-center gap-2 mb-4">
            <Activity size={14} className="text-cyan" />
            <h2 className="text-slate-100 font-semibold text-sm">NIFTY Options — PCR</h2>
          </div>
          <div className="flex items-baseline gap-3 mb-5">
            <span className="text-4xl font-extrabold tabular-nums" style={{
              color: pcr == null ? '#4E6280' : pcr > 1.2 ? '#10B981' : pcr < 0.8 ? '#F43F5E' : '#F59E0B',
            }}>
              {pcr?.toFixed(2) ?? '—'}
            </span>
            <span className="text-muted text-sm">{pcrLabel(pcr)}</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1.5">Support</p>
              {support.length === 0 ? (
                <p className="text-muted text-xs">—</p>
              ) : support.map((lvl, i) => (
                <p key={i} className="text-profit font-semibold text-sm tabular-nums">
                  {fmtIdx(lvl)}
                </p>
              ))}
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1.5">Resistance</p>
              {resist.length === 0 ? (
                <p className="text-muted text-xs">—</p>
              ) : resist.map((lvl, i) => (
                <p key={i} className="text-loss font-semibold text-sm tabular-nums">
                  {fmtIdx(lvl)}
                </p>
              ))}
            </div>
          </div>
        </div>

        {/* Max Pain panel */}
        <div className="rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
          <div className="flex items-center gap-2 mb-4">
            <BarChart2 size={14} className="text-cyan" />
            <h2 className="text-slate-100 font-semibold text-sm">Max Pain Strike — NIFTY</h2>
          </div>
          <div className="flex gap-8 mb-4">
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Spot</p>
              <p className="text-2xl font-extrabold tabular-nums text-slate-100">
                {fmtIdx(spot)}
              </p>
            </div>
            <div className="w-px bg-border" />
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Max Pain</p>
              <p className="text-2xl font-extrabold tabular-nums text-warn">
                {fmtIdx(maxPain)}
              </p>
            </div>
          </div>
          {mpDiff != null && (
            <p className="text-sm rounded-lg px-4 py-3 border" style={{
              background: mpDiff > 0 ? 'rgba(244,63,94,0.07)' : 'rgba(16,185,129,0.07)',
              borderColor: mpDiff > 0 ? 'rgba(244,63,94,0.22)' : 'rgba(16,185,129,0.22)',
              color:       mpDiff > 0 ? '#F43F5E' : '#10B981',
            }}>
              Spot {mpDiff > 0 ? 'above' : 'below'} Max Pain by{' '}
              <strong>
                {Math.abs(mpDiff).toLocaleString('en-IN', { maximumFractionDigits: 0 })} pts
              </strong>
              {' '}— price likely to {mpDiff > 0 ? 'fall' : 'rise'} towards {fmtIdx(maxPain)} by expiry
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
