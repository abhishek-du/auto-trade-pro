import { useEffect, useState, useCallback } from 'react';
import {
  RefreshCw, TrendingUp, TrendingDown, XCircle, CheckCircle, ShieldCheck,
  ShieldAlert, ChevronDown, ChevronRight, Newspaper, Wrench, Search,
} from 'lucide-react';
import { apiFetch } from '../api/client';
import { fmtIST } from '../utils/datetime';

const num = (n) => (n == null ? '—' : Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 }));
const todayISO = () => new Date().toISOString().slice(0, 10);

function ActionChip({ action }) {
  const a = (action || '').toUpperCase();
  const cls = a === 'BUY' ? 'text-profit bg-profit/10 border-profit/30'
            : a === 'SELL' ? 'text-loss bg-loss/10 border-loss/30'
            : 'text-muted bg-white/5 border-border';
  const Icon = a === 'BUY' ? TrendingUp : a === 'SELL' ? TrendingDown : XCircle;
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-bold px-2 py-0.5 rounded border ${cls}`}>
      <Icon size={12} /> {a || '—'}
    </span>
  );
}

function GroundBadge({ g }) {
  if (!g || g.grounded == null) return null;
  if (g.grounded) return (
    <span className="inline-flex items-center gap-1 text-[10px] text-profit"><ShieldCheck size={12} /> grounded</span>
  );
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-amber-400" title={(g.unsupported_claims || []).join(' | ')}>
      <ShieldAlert size={12} /> {g.soft_failed ? 'soft-fail (proceeded)' : 'ungrounded'}
    </span>
  );
}

function Stat({ label, value, tone }) {
  return (
    <div className="flex flex-col px-3 py-2 rounded-lg bg-white/5 border border-border min-w-[80px]">
      <span className="text-[10px] uppercase tracking-wide text-muted">{label}</span>
      <span className={`text-lg font-bold ${tone || ''}`}>{value}</span>
    </div>
  );
}

function DecisionCard({ d }) {
  const [open, setOpen] = useState(false);
  const took = d.action === 'BUY' || d.action === 'SELL';
  return (
    <div className={`rounded-xl border bg-white/[0.03] ${took ? 'border-profit/20' : 'border-border'}`}>
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-start gap-3 p-3 text-left">
        <div className="pt-0.5">{open ? <ChevronDown size={16} className="text-muted" /> : <ChevronRight size={16} className="text-muted" />}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold">{d.symbol}</span>
            <ActionChip action={d.action} />
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 border border-border text-muted">{d.source}</span>
            {d.confidence > 0 && <span className="text-[11px] font-mono text-muted">conf {d.confidence}%</span>}
            <GroundBadge g={d.grounding} />
            <span className="ml-auto text-[10px] text-muted font-mono">{fmtIST(d.ts)}</span>
          </div>
          {d.headline && (
            <div className="flex items-start gap-1.5 mt-1.5 text-[12px] text-slate-300">
              <Newspaper size={13} className="text-sky-400 shrink-0 mt-0.5" />
              <span className="line-clamp-2">{d.headline}</span>
            </div>
          )}
          <div className="mt-1.5 text-[12px]">
            {took
              ? <span className="text-profit">✓ {d.verdict === 'TAKE' ? 'Taken' : d.verdict} — {d.thesis || d.bull || 'executed'}</span>
              : <span className="text-muted">✗ Skipped — <span className="text-amber-300/90">{d.skip_reason || d.key_risk || 'did not meet criteria'}</span></span>}
          </div>
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 ml-7 space-y-3 text-[12.5px] border-t border-border/50">
          {d.entry != null && (
            <div className="flex gap-4 pt-2 font-mono text-[11px] text-muted">
              <span>price@decision: <span className="text-slate-200">₹{num(d.entry)}</span></span>
              {d.stop != null && <span>SL: ₹{num(d.stop)}</span>}
              {d.target != null && <span>target: ₹{num(d.target)}</span>}
              {d.regime && <span>regime: {d.regime}</span>}
            </div>
          )}
          {d.summary && <div><div className="text-[10px] uppercase text-muted mb-0.5">News summary</div><p className="text-slate-300">{d.summary}</p></div>}
          <div className="grid sm:grid-cols-2 gap-3">
            {d.bull && <div><div className="text-[10px] uppercase text-profit mb-0.5">Bull case</div><p className="text-slate-300">{d.bull}</p></div>}
            {d.bear && <div><div className="text-[10px] uppercase text-loss mb-0.5">Bear case</div><p className="text-slate-300">{d.bear}</p></div>}
          </div>
          {d.thesis && <div><div className="text-[10px] uppercase text-muted mb-0.5">Thesis</div><p className="text-slate-300">{d.thesis}</p></div>}
          {d.key_risk && <div><div className="text-[10px] uppercase text-amber-400 mb-0.5">Key risk</div><p className="text-slate-300">{d.key_risk}</p></div>}
          {d.market_confirmation && <div className="text-[11px] text-muted">Market confirmation: <span className="text-slate-300">{d.market_confirmation}</span></div>}
          {d.grounding && d.grounding.grounded === false && (d.grounding.unsupported_claims || []).length > 0 && (
            <div>
              <div className="text-[10px] uppercase text-amber-400 mb-0.5">Ungrounded claims (stripped as proof-check)</div>
              <ul className="list-disc ml-4 text-amber-200/80 space-y-0.5">{d.grounding.unsupported_claims.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
          )}
          {(d.tools_used || []).length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <Wrench size={12} className="text-muted" />
              {d.tools_used.map((t, i) => <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 border border-border text-muted">{t}</span>)}
            </div>
          )}
          {d.model_reasoning && (
            <details className="text-[11px]">
              <summary className="cursor-pointer text-muted hover:text-slate-300">Model reasoning (raw)</summary>
              <pre className="whitespace-pre-wrap text-slate-400 mt-1 font-mono text-[10.5px] leading-relaxed">{d.model_reasoning}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default function NewsDecisions() {
  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState({ BUY: 0, SELL: 0, SKIP: 0 });
  const [loading, setLoading] = useState(true);
  const [f, setF] = useState({
    date_from: todayISO(), date_to: todayISO(), action: '', source: '',
    symbol: '', min_confidence: '', grounded: '',
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p = new URLSearchParams();
      Object.entries(f).forEach(([k, v]) => { if (v !== '' && v != null) p.set(k, v); });
      p.set('limit', '500');
      const d = await apiFetch(`/api/v1/agent/journal?${p.toString()}`);
      setRows(d.decisions || []);
      setCounts(d.counts || { BUY: 0, SELL: 0, SKIP: 0 });
    } catch (e) {
      setRows([]); setCounts({ BUY: 0, SELL: 0, SKIP: 0 });
    } finally { setLoading(false); }
  }, [f]);

  useEffect(() => { load(); }, [load]);

  const set = (k) => (e) => setF(s => ({ ...s, [k]: e.target.value }));
  const inputCls = 'bg-white/5 border border-border rounded-lg px-2.5 py-1.5 text-[12px] text-slate-200 focus:outline-none focus:border-sky-500/50';

  return (
    <div className="max-w-6xl mx-auto space-y-5">
      <div className="flex items-center gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2"><Newspaper size={20} className="text-sky-400" /> News Decision Journal</h1>
          <p className="text-[12px] text-muted">Har processed stock — buy / sell / skip — poori reasoning aur proof ke saath.</p>
        </div>
        <button onClick={load} className="ml-auto inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-lg bg-white/5 border border-border hover:bg-white/10">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      <div className="flex gap-2.5 flex-wrap">
        <Stat label="Buy"  value={counts.BUY}  tone="text-profit" />
        <Stat label="Sell" value={counts.SELL} tone="text-loss" />
        <Stat label="Skip" value={counts.SKIP} tone="text-muted" />
        <Stat label="Total" value={rows.length} />
      </div>

      {/* Filters */}
      <div className="flex gap-2.5 flex-wrap items-end p-3 rounded-xl bg-white/[0.03] border border-border">
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted">From
          <input type="date" value={f.date_from} onChange={set('date_from')} className={inputCls} /></label>
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted">To
          <input type="date" value={f.date_to} onChange={set('date_to')} className={inputCls} /></label>
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted">Action
          <select value={f.action} onChange={set('action')} className={inputCls}>
            <option value="">All</option><option>BUY</option><option>SELL</option><option>SKIP</option>
          </select></label>
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted">Source
          <select value={f.source} onChange={set('source')} className={inputCls}>
            <option value="">All</option><option value="NEWS">News</option><option value="HUB_SIGNAL">Hub</option>
          </select></label>
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted">Grounding
          <select value={f.grounded} onChange={set('grounded')} className={inputCls}>
            <option value="">All</option><option value="true">Grounded</option><option value="false">Ungrounded/soft-fail</option>
          </select></label>
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted">Min conf
          <input type="number" value={f.min_confidence} onChange={set('min_confidence')} placeholder="0" className={`${inputCls} w-20`} /></label>
        <label className="flex flex-col gap-1 text-[10px] uppercase text-muted flex-1 min-w-[140px]">Symbol
          <div className="relative">
            <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
            <input value={f.symbol} onChange={set('symbol')} placeholder="e.g. LT, AUBANK" className={`${inputCls} w-full pl-7`} />
          </div></label>
      </div>

      {loading ? (
        <div className="text-center py-16 text-muted">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-center py-16 text-muted">
          Is filter pe koi decision nahi mila. News engine jaise-jaise stocks process karega, yahan aate rahenge.
        </div>
      ) : (
        <div className="space-y-2.5">
          {rows.map((d) => <DecisionCard key={d.id} d={d} />)}
        </div>
      )}
    </div>
  );
}
