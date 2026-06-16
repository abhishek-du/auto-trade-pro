import { useState, useEffect, useCallback } from 'react';
import { Layers, TrendingUp, TrendingDown, Activity, Gauge, ShieldAlert, Brain, Newspaper, CandlestickChart as ChartIcon } from 'lucide-react';
import { apiFetch } from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import CandlestickChart from '../components/chart/CandlestickChart';
import { formatINR } from '../utils/indianFormat';

const INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY'];
// Index → yfinance candle symbol the backend stores.
const INDEX_CANDLE = { NIFTY: '^NSEI', BANKNIFTY: '^NSEBANK', FINNIFTY: '^NSEI' };
const fmt = (n, d = 2) => formatINR(n ?? 0, d);
const num = (n, d = 2) => (n == null ? '—' : Number(n).toFixed(d));
const fmtDateTime = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
  catch { return s; }
};
const moodColor = (m) => ({
  BULLISH: 'text-profit', POSITIVE: 'text-profit', CALM: 'text-profit',
  BEARISH: 'text-loss', NEGATIVE: 'text-loss', FEARFUL: 'text-loss',
  ELEVATED: 'text-amber-400', MIXED: 'text-amber-400', NEUTRAL: 'text-slate-300',
}[m] || 'text-slate-300');

function StatCard({ label, value, sub, color = 'text-slate-100', Icon }) {
  return (
    <div className="bg-panel border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-muted text-xs font-medium">{label}</span>
        {Icon && <Icon size={15} className={color} />}
      </div>
      <p className={`text-xl font-bold tabular-nums ${color}`}>{value}</p>
      {sub && <p className="text-muted text-xs mt-1 truncate">{sub}</p>}
    </div>
  );
}

// ── F&O Positions (detailed, expandable) ──────────────────────────────────────
function Field({ label, value, color = 'text-slate-200', sub }) {
  return (
    <div className="bg-surface/40 rounded-lg px-3 py-2">
      <p className="text-[9px] text-muted uppercase tracking-wider">{label}</p>
      <p className={`text-sm font-semibold tabular-nums ${color}`}>{value}</p>
      {sub && <p className="text-[9px] text-muted">{sub}</p>}
    </div>
  );
}

function FnOPositionRow({ p }) {
  const [open, setOpen] = useState(false);
  const gain = (p.pnl ?? 0) >= 0;
  const buy = (p.direction ?? '').toUpperCase() === 'BUY';
  const isOpt = p.option_type === 'CE' || p.option_type === 'PE';
  const g = p.greeks || {};
  return (
    <>
      <tr onClick={() => setOpen(!open)} className="border-b border-border/50 hover:bg-surface/40 cursor-pointer">
        <td className="px-4 py-2.5 text-muted text-xs tabular-nums whitespace-nowrap">{fmtDateTime(p.opened_at)}</td>
        <td className="px-4 py-2.5 font-medium text-slate-200">{p.underlying}</td>
        <td className="px-4 py-2.5">
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${buy ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss'}`}>{buy ? 'BUY' : 'SELL'}</span>
        </td>
        <td className="px-4 py-2.5">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${p.instrument_type === 'FUTURE' ? 'bg-blue-500/20 text-blue-300' : p.option_type === 'CE' ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss'}`}>
            {p.instrument_type === 'FUTURE' ? 'FUT' : p.option_type}
          </span>
        </td>
        <td className="px-4 py-2.5 text-slate-300 text-xs tabular-nums">
          {p.strike ? `${num(p.strike, 0)} · ` : ''}{p.expiry?.slice(5) ?? '—'}
          {p.moneyness && <span className={`ml-1 ${p.moneyness === 'ITM' ? 'text-profit' : 'text-muted'}`}>{p.moneyness}</span>}
        </td>
        <td className="px-4 py-2.5 tabular-nums text-slate-300">{p.lots ?? '—'}</td>
        <td className="px-4 py-2.5 tabular-nums text-slate-300">{num(p.entry)}</td>
        <td className="px-4 py-2.5 tabular-nums text-slate-100">{num(p.current)}</td>
        <td className={`px-4 py-2.5 tabular-nums font-semibold ${gain ? 'text-profit' : 'text-loss'}`}>
          {gain ? '+' : ''}{fmt(p.pnl)} <span className="text-[10px] opacity-70">({num(p.pnl_pct, 1)}%)</span>
        </td>
        <td className="px-4 py-2.5 tabular-nums text-muted text-xs">{fmt(p.margin)}</td>
      </tr>
      {open && (
        <tr className="bg-[#080e1c]">
          <td colSpan={10} className="px-5 py-4">
            <div className="space-y-3">
              {/* Contract line */}
              <p className="text-sm text-slate-200 font-semibold">
                {p.underlying} {p.strike ? num(p.strike, 0) : ''} {p.instrument_type === 'FUTURE' ? 'FUT' : p.option_type}
                <span className="text-muted font-normal text-xs"> · expiry {p.expiry?.slice(0, 10)} · {p.dte ?? '?'} days to expiry · lot {p.lot_size}</span>
              </p>
              {/* Premium / position */}
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
                <Field label={isOpt ? 'Entry Premium' : 'Entry'} value={`₹${num(p.entry)}`} />
                <Field label={isOpt ? 'Current Premium' : 'Current'} value={`₹${num(p.current)}`} color={gain ? 'text-profit' : 'text-loss'} />
                <Field label="Lots × Size" value={`${p.lots} × ${p.lot_size}`} sub={`${num(p.qty, 0)} qty`} />
                <Field label={isOpt ? 'Premium Paid' : 'Notional'} value={fmt(p.premium_paid)} sub={isOpt ? '= max loss' : ''} />
                <Field label="Current Value" value={fmt(p.current_value)} />
                <Field label="P&L" value={`${gain ? '+' : ''}${fmt(p.pnl)}`} color={gain ? 'text-profit' : 'text-loss'} sub={`${num(p.pnl_pct, 2)}%`} />
              </div>
              {/* Levels */}
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
                <Field label="Stop Loss" value={`₹${num(p.stop_loss)}`} color="text-loss" />
                <Field label="Target" value={`₹${num(p.take_profit)}`} color="text-profit" />
                {p.breakeven != null && <Field label="Breakeven" value={num(p.breakeven, 0)} sub="underlying" />}
                {p.spot != null && <Field label="Underlying Spot" value={num(p.spot, 0)} />}
                <Field label="Margin Blocked" value={fmt(p.margin)} />
                <Field label="Opened" value={fmtDateTime(p.opened_at)} />
              </div>
              {/* Greeks */}
              {isOpt && (g.delta != null || g.iv != null) && (
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-muted mb-1.5">Option Greeks (live)</p>
                  <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
                    <Field label="IV" value={g.iv != null ? `${num(g.iv, 1)}%` : '—'} color="text-amber-400" />
                    <Field label="Delta (Δ)" value={g.delta != null ? num(g.delta, 3) : '—'} />
                    <Field label="Gamma (Γ)" value={g.gamma != null ? num(g.gamma, 4) : '—'} />
                    <Field label="Theta (Θ)" value={g.theta != null ? num(g.theta, 1) : '—'} color="text-loss" sub="₹/day decay" />
                    <Field label="Vega (ν)" value={g.vega != null ? num(g.vega, 1) : '—'} sub="per 1% IV" />
                  </div>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function PositionsPanel({ data }) {
  if (!data || data.count === 0) {
    return (
      <div className="bg-panel border border-border rounded-xl p-6 text-center text-muted text-sm space-y-1">
        <p>No open F&O positions right now.</p>
        <p className="text-xs">
          F&O trading is <span className="text-profit font-semibold">enabled</span> and Zerodha&nbsp;Kite
          is <span className="text-profit font-semibold">connected</span>. The agent opens an index
          option position when its multi-factor signal triggers (see Signals &amp; Predictions below).
        </p>
      </div>
    );
  }
  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
          <Layers size={15} className="text-cyan" /> F&O Positions
          <span className="text-xs text-muted">{data.count} open · click a row for full detail</span>
        </h2>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-muted">Margin: <span className="text-slate-300">{fmt(data.total_margin)}</span></span>
          <span className={data.total_pnl >= 0 ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
            {data.total_pnl >= 0 ? '+' : ''}{fmt(data.total_pnl)} P&L
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted text-xs uppercase tracking-wider">
              {['Opened', 'Instrument', 'Side', 'Type', 'Strike/Expiry', 'Lots', 'Entry', 'Current', 'P&L', 'Margin'].map((h) => (
                <th key={h} className="text-left px-4 py-2.5 font-semibold whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.positions.map((p, i) => <FnOPositionRow key={i} p={p} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Option Chain ──────────────────────────────────────────────────────────────
function ChainPanel({ chain, ivRank }) {
  if (!chain || !chain.strikes?.length) {
    return (
      <div className="bg-panel border border-border rounded-xl p-6 text-center text-muted text-sm">
        No chain data yet for {chain?.underlying}. Greeks populate when the options task runs with
        <code className="text-cyan"> ENABLE_FNO=true</code>.
      </div>
    );
  }
  const atm = chain.spot
    ? chain.strikes.reduce((a, b) => Math.abs(b.strike - chain.spot) < Math.abs(a.strike - chain.spot) ? b : a)
    : null;
  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-slate-200">
          {chain.underlying} Chain
          <span className="ml-2 text-xs text-muted">Spot {num(chain.spot, 0)} · Exp {chain.expiry?.slice(5)}</span>
        </h2>
        {ivRank?.iv_rank != null && (
          <div className="flex items-center gap-2 text-xs">
            <Gauge size={13} className="text-amber-400" />
            <span className="text-muted">IV {num(ivRank.atm_iv * 100, 1)}% · Rank</span>
            <span className={`font-bold ${ivRank.iv_rank < 30 ? 'text-profit' : ivRank.iv_rank > 70 ? 'text-loss' : 'text-amber-400'}`}>
              {num(ivRank.iv_rank, 0)}
            </span>
          </div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted uppercase tracking-wider">
              <th className="px-2 py-2 text-right text-profit">CE Δ</th>
              <th className="px-2 py-2 text-right text-profit">CE IV</th>
              <th className="px-2 py-2 text-right text-profit">CE LTP</th>
              <th className="px-3 py-2 text-center text-slate-200">Strike</th>
              <th className="px-2 py-2 text-left text-loss">PE LTP</th>
              <th className="px-2 py-2 text-left text-loss">PE IV</th>
              <th className="px-2 py-2 text-left text-loss">PE Δ</th>
            </tr>
          </thead>
          <tbody>
            {chain.strikes.map((s) => {
              const isAtm = atm && s.strike === atm.strike;
              return (
                <tr key={s.strike} className={`border-b border-border/40 ${isAtm ? 'bg-cyan/[0.06]' : 'hover:bg-surface/30'}`}>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-400">{num(s.ce_delta)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-400">{s.ce_iv ? num(s.ce_iv * 100, 1) : '—'}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-profit font-medium">{num(s.ce_ltp)}</td>
                  <td className={`px-3 py-1.5 text-center tabular-nums font-bold ${isAtm ? 'text-cyan' : 'text-slate-200'}`}>{num(s.strike, 0)}</td>
                  <td className="px-2 py-1.5 text-left tabular-nums text-loss font-medium">{num(s.pe_ltp)}</td>
                  <td className="px-2 py-1.5 text-left tabular-nums text-slate-400">{s.pe_iv ? num(s.pe_iv * 100, 1) : '—'}</td>
                  <td className="px-2 py-1.5 text-left tabular-nums text-slate-400">{num(s.pe_delta)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Signals & Predictions ─────────────────────────────────────────────────────
function SignalsPanel({ signals }) {
  if (!signals?.length) return null;
  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
          <TrendingUp size={15} className="text-cyan" /> Signals & Predictions
          <span className="text-xs text-muted font-normal">— what the agent would trade</span>
        </h2>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-border">
        {signals.map((s) => {
          const buy = s.direction === 'BUY';
          const sell = s.direction === 'SELL';
          const sug = s.suggestion || {};
          const dirColor = buy ? 'text-profit' : sell ? 'text-loss' : 'text-muted';
          const dirBg = buy ? 'bg-profit/15' : sell ? 'bg-loss/15' : 'bg-surface';
          return (
            <div key={s.underlying} className="p-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-slate-100">{s.underlying}</span>
                <span className={`text-[11px] font-bold px-2 py-0.5 rounded ${dirBg} ${dirColor}`}>
                  {buy ? '▲ BULLISH' : sell ? '▼ BEARISH' : '◆ NEUTRAL'}
                </span>
              </div>
              {sug.action ? (
                <div className={`text-sm font-bold ${dirColor}`}>
                  {sug.action} {sug.strike != null ? num(sug.strike, 0) : ''}
                  {sug.premium != null && <span className="text-muted font-normal text-xs"> @ ₹{num(sug.premium)}</span>}
                </div>
              ) : <div className="text-sm text-muted">No directional trade</div>}
              {sug.stop != null && (
                <div className="flex gap-3 text-[11px] text-muted">
                  <span>SL <span className="text-loss">₹{num(sug.stop)}</span></span>
                  <span>TP <span className="text-profit">₹{num(sug.target)}</span></span>
                </div>
              )}
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted pt-1 border-t border-border/40">
                <span>Conf <span className="text-slate-300">{num(s.confidence, 0)}</span></span>
                {s.composite_score != null && <span>Score <span className={s.composite_score >= 0 ? 'text-profit' : 'text-loss'}>{s.composite_score > 0 ? '+' : ''}{num(s.composite_score, 0)}</span></span>}
                <span>PCR <span className="text-slate-300">{s.pcr ?? '—'}</span></span>
                <span>MaxPain <span className="text-slate-300">{s.max_pain != null ? num(s.max_pain, 0) : '—'}</span></span>
                <span>IVr <span className={s.iv_rank < 30 ? 'text-profit' : s.iv_rank > 70 ? 'text-loss' : 'text-amber-400'}>{s.iv_rank != null ? num(s.iv_rank, 0) : '—'}</span></span>
              </div>
              {/* Factor breakdown — what the agent is weighing */}
              {s.factors?.length > 0 && (
                <div className="pt-1.5 space-y-1">
                  <p className="text-[9px] uppercase tracking-wider text-muted">Decision factors</p>
                  {s.factors.map((f) => (
                    <div key={f.factor} className="flex items-center gap-2 text-[10px]">
                      <span className="w-16 text-muted capitalize shrink-0">{f.factor.replace(/_/g, ' ')}</span>
                      <div className="flex-1 h-1 bg-surface rounded-full overflow-hidden">
                        <div className={`h-full ${f.score >= 0 ? 'bg-profit' : 'bg-loss'}`}
                          style={{ width: `${Math.min(100, Math.abs(f.score) * 4)}%`, marginLeft: f.score < 0 ? 'auto' : 0 }} />
                      </div>
                      <span className={`w-7 text-right tabular-nums ${f.score >= 0 ? 'text-profit' : f.score < 0 ? 'text-loss' : 'text-muted'}`}>
                        {f.score > 0 ? '+' : ''}{num(f.score, 0)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── AI Analysis ───────────────────────────────────────────────────────────────
function AIAnalysisPanel({ analysis, underlying }) {
  return (
    <div className="bg-panel border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-3">
        <Brain size={15} className="text-violet-400" />
        <h2 className="text-sm font-semibold text-slate-200">AI Desk Analysis — {underlying}</h2>
        <span className="ml-auto text-[10px] text-muted">live · 60s refresh</span>
      </div>
      {analysis ? (
        <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">{analysis}</p>
      ) : (
        <p className="text-sm text-muted italic">Generating analysis… (local model — may take a few seconds). Refresh shortly.</p>
      )}
    </div>
  );
}

// ── Market Sentiment ──────────────────────────────────────────────────────────
function SentimentPanel({ sentiment, newsMood }) {
  if (!sentiment) return null;
  const s = sentiment;
  const cells = [
    { label: 'India VIX', value: s.india_vix != null ? num(s.india_vix, 2) : '—', tag: s.vix_regime, color: moodColor(s.vix_regime) },
    { label: 'Breadth', value: `${s.advances ?? '—'}/${s.declines ?? '—'}`, tag: s.breadth_mood, color: moodColor(s.breadth_mood) },
    { label: 'PCR', value: s.pcr ?? '—', tag: s.pcr_bias, color: moodColor(s.pcr_bias) },
    { label: 'IV Rank', value: s.iv_rank != null ? num(s.iv_rank, 0) : '—', tag: s.iv_rank < 30 ? 'CHEAP' : s.iv_rank > 70 ? 'RICH' : 'FAIR', color: s.iv_rank < 30 ? 'text-profit' : s.iv_rank > 70 ? 'text-loss' : 'text-amber-400' },
    { label: 'Max Pain', value: s.max_pain != null ? num(s.max_pain, 0) : '—', tag: 'gravity', color: 'text-slate-300' },
    { label: 'News Mood', value: newsMood ?? '—', tag: '', color: moodColor(newsMood) },
  ];
  return (
    <div className="bg-panel border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-3">
        <Activity size={15} className="text-cyan" />
        <h2 className="text-sm font-semibold text-slate-200">Market Sentiment</h2>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {cells.map((c) => (
          <div key={c.label} className="bg-surface/40 rounded-lg p-3">
            <p className="text-[10px] text-muted uppercase tracking-wider">{c.label}</p>
            <p className="text-lg font-bold text-slate-100 tabular-nums">{c.value}</p>
            {c.tag && <p className={`text-[11px] font-semibold ${c.color}`}>{c.tag}</p>}
          </div>
        ))}
      </div>
      {s.fii_dii && (
        <div className="mt-3 pt-3 border-t border-border/40 flex gap-5 text-xs">
          <span className="text-muted">FII net: <span className={`font-semibold ${(s.fii_dii.fii_net ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>₹{num(s.fii_dii.fii_net, 0)} Cr</span></span>
          <span className="text-muted">DII net: <span className={`font-semibold ${(s.fii_dii.dii_net ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>₹{num(s.fii_dii.dii_net, 0)} Cr</span></span>
          <span className="text-muted ml-auto">{s.fii_dii.date}</span>
        </div>
      )}
    </div>
  );
}

// ── News ──────────────────────────────────────────────────────────────────────
function NewsPanel({ news }) {
  if (!news?.length) return (
    <div className="bg-panel border border-border rounded-xl p-5 text-sm text-muted">No recent news.</div>
  );
  const dot = (sent) => sent === 'positive' ? 'bg-profit' : sent === 'negative' ? 'bg-loss' : 'bg-slate-500';
  return (
    <div className="bg-panel border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-3">
        <Newspaper size={15} className="text-blue-400" />
        <h2 className="text-sm font-semibold text-slate-200">Market News</h2>
        <span className="ml-auto text-[10px] text-muted">{news.length} items</span>
      </div>
      <div className="space-y-2.5 max-h-96 overflow-y-auto">
        {news.map((n, i) => (
          <a key={i} href={n.url || '#'} target="_blank" rel="noreferrer"
            className="flex items-start gap-2.5 group">
            <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${dot(n.sentiment)}`} />
            <div className="min-w-0">
              <p className="text-sm text-slate-300 group-hover:text-cyan leading-snug">{n.headline}</p>
              <p className="text-[10px] text-muted mt-0.5">
                {n.source} · {fmtDateTime(n.published_at)}
                {n.sentiment && <span className={`ml-2 ${moodColor(n.sentiment.toUpperCase())}`}>{n.sentiment} ({num(n.score, 2)})</span>}
              </p>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

export default function FnO() {
  const [underlying, setUnderlying] = useState('NIFTY');
  const [positions, setPositions] = useState(null);
  const [chain, setChain] = useState(null);
  const [ivRank, setIvRank] = useState(null);
  const [signals, setSignals] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [pos, ch, iv, sig, ana] = await Promise.all([
        apiFetch('/api/v1/india/fno/positions').catch(() => null),
        apiFetch(`/api/v1/india/fno/chain/${underlying}`).catch(() => null),
        apiFetch(`/api/v1/india/fno/iv-rank/${underlying}`).catch(() => null),
        apiFetch('/api/v1/india/fno/signals').catch(() => null),
        apiFetch(`/api/v1/india/fno/analysis/${underlying}`).catch(() => null),
      ]);
      setPositions(pos); setChain(ch); setIvRank(iv); setSignals(sig?.signals ?? null); setAnalysis(ana);
    } finally { setLoading(false); }
  }, [underlying]);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2">
          <Layers size={20} className="text-cyan" /> Futures & Options
        </h1>
        <div className="flex rounded-lg overflow-hidden border border-border">
          {INDICES.map((idx) => (
            <button key={idx} onClick={() => setUnderlying(idx)}
              className={`px-4 py-1.5 text-xs font-medium transition-colors ${
                underlying === idx ? 'bg-accent text-white' : 'text-muted hover:bg-surface'}`}>
              {idx}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Open F&O P&L" Icon={positions?.total_pnl >= 0 ? TrendingUp : TrendingDown}
          color={positions?.total_pnl >= 0 ? 'text-profit' : 'text-loss'}
          value={`${(positions?.total_pnl ?? 0) >= 0 ? '+' : ''}${fmt(positions?.total_pnl)}`}
          sub={`${positions?.count ?? 0} open positions`} />
        <StatCard label="Margin Blocked" Icon={ShieldAlert} color="text-amber-400"
          value={fmt(positions?.total_margin)} sub="SPAN + exposure (approx)" />
        <StatCard label={`${underlying} ATM IV`} Icon={Activity} color="text-blue-400"
          value={ivRank?.atm_iv != null ? `${num(ivRank.atm_iv * 100, 1)}%` : '—'}
          sub={ivRank?.history?.length ? `${ivRank.history.length} days history` : 'no history yet'} />
        <StatCard label="IV Rank" Icon={Gauge}
          color={ivRank?.iv_rank < 30 ? 'text-profit' : ivRank?.iv_rank > 70 ? 'text-loss' : 'text-amber-400'}
          value={ivRank?.iv_rank != null ? num(ivRank.iv_rank, 0) : '—'}
          sub={ivRank?.iv_rank < 30 ? 'cheap — buy vol' : ivRank?.iv_rank > 70 ? 'rich — sell vol' : 'neutral'} />
      </div>

      {/* Live index candlestick chart */}
      <div className="bg-panel border border-border rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <ChartIcon size={15} className="text-cyan" />
          <h2 className="text-sm font-semibold text-slate-200">{underlying} Chart</h2>
          <span className="text-[10px] text-muted ml-auto">candles · indicators · live</span>
        </div>
        <CandlestickChart
          key={underlying}
          symbol={INDEX_CANDLE[underlying]}
          name={underlying}
          defaultTimeframe="1d"
          height={460}
          showIndicators
          showVolume
          embedded
        />
      </div>

      <SignalsPanel signals={signals} />

      {/* AI analysis + sentiment side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <AIAnalysisPanel analysis={analysis?.ai_analysis} underlying={underlying} />
        <SentimentPanel sentiment={analysis?.sentiment} newsMood={analysis?.news_mood} />
      </div>

      <PositionsPanel data={positions} />
      <ChainPanel chain={chain} ivRank={ivRank} />
      <NewsPanel news={analysis?.news} />
    </div>
  );
}
