import { useState, useEffect } from 'react';
import { Filter, Search } from 'lucide-react';
import SignalBadge    from '../components/SignalBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import { listIndiaFundamentals } from '../api/client';

function fmtOpt(v, digits = 1) {
  if (v == null) return null;
  return (+v).toFixed(digits);
}

function scoreSignal(score) {
  if (score == null) return { signal_type: 'HOLD' };
  if (score >= 70)   return { signal_type: 'BUY' };
  if (score < 40)    return { signal_type: 'SELL' };
  return { signal_type: 'HOLD' };
}

function ScoreCell({ score }) {
  if (score == null) return <span className="text-muted">—</span>;
  const s = +score;
  const cls = s >= 70 ? 'text-profit bg-profit/10'
            : s >= 40 ? 'text-warn bg-warn/10'
            : 'text-loss bg-loss/10';
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-bold tabular-nums ${cls}`}>
      {s.toFixed(0)}
    </span>
  );
}

function NumCell({ value, highlight, positiveGood = true, digits = 1 }) {
  const fmt = fmtOpt(value, digits);
  if (fmt == null) return <span className="text-muted">—</span>;
  const flag = highlight != null ? highlight : false;
  const col  = flag ? (positiveGood ? 'text-profit' : 'text-loss') : '';
  return <span className={col || 'text-slate-200'}>{fmt}</span>;
}

const PRESETS = [
  { id: 'pe25',  label: 'PE < 25',   fn: r => r.pe_ratio        != null && r.pe_ratio < 25 },
  { id: 'roe15', label: 'ROE > 15%', fn: r => r.roe             != null && r.roe > 15 },
  { id: 'debt1', label: 'Debt < 1',  fn: r => r.debt_to_equity  != null && r.debt_to_equity < 1 },
];

export default function IndiaFundamentals() {
  const [all,     setAll]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [search,  setSearch]  = useState('');
  const [active,  setActive]  = useState(new Set());

  useEffect(() => {
    listIndiaFundamentals()
      .then(data => setAll(Array.isArray(data) ? data : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const togglePreset = id => setActive(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const filtered = all.filter(r => {
    if (search) {
      const q = search.toLowerCase();
      const sym = (r.symbol ?? '').toLowerCase().replace('.ns', '');
      const co  = (r.company_name ?? '').toLowerCase();
      if (!sym.includes(q) && !co.includes(q)) return false;
    }
    for (const id of active) {
      const preset = PRESETS.find(p => p.id === id);
      if (preset && !preset.fn(r)) return false;
    }
    return true;
  });

  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-5 fade-in">

      {/* ── Filter bar ─────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border px-4 py-3 flex flex-wrap items-center gap-3"
        style={{ background: '#0F1829' }}>
        {/* Search */}
        <div className="flex items-center gap-2 flex-1 min-w-[160px]">
          <Search size={13} className="text-muted shrink-0" />
          <input
            type="text"
            placeholder="Search symbol or company…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="flex-1 bg-transparent border-0 outline-none text-sm text-slate-200 placeholder:text-muted"
          />
        </div>

        {/* Preset filters */}
        <div className="flex items-center gap-2">
          <Filter size={12} className="text-muted shrink-0" />
          {PRESETS.map(p => (
            <button key={p.id} onClick={() => togglePreset(p.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                active.has(p.id)
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'bg-surface text-muted border border-border hover:text-slate-300 hover:bg-white/5'
              }`}>
              {p.label}
            </button>
          ))}
        </div>

        <span className="text-muted text-xs ml-auto">{filtered.length} stocks</span>
      </div>

      {/* ── Table ──────────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
        {all.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-2">
            <p className="text-muted text-sm">No fundamental data in database</p>
            <p className="text-muted/50 text-xs">
              POST /api/v1/india/fundamentals/refresh to run the weekly update
            </p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <p className="text-muted text-sm">No stocks match the current filters</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted text-[10px] uppercase tracking-wider">
                  <th className="text-left  px-5 py-3 sticky left-0 bg-[#0F1829]">Symbol</th>
                  <th className="text-right px-4 py-3">PE</th>
                  <th className="text-right px-4 py-3">ROE %</th>
                  <th className="text-right px-4 py-3">ROCE %</th>
                  <th className="text-right px-4 py-3">D/E</th>
                  <th className="text-right px-4 py-3">Promoter %</th>
                  <th className="text-right px-4 py-3">Pledged %</th>
                  <th className="text-right px-4 py-3">F-Score</th>
                  <th className="text-center px-5 py-3">Signal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filtered.map((r, i) => (
                  <tr key={i} className="hover:bg-white/2 transition-colors">
                    <td className="px-5 py-3 sticky left-0 bg-[#0F1829]">
                      <p className="font-semibold text-slate-200">
                        {r.symbol?.replace('.NS', '')}
                      </p>
                      {r.company_name && (
                        <p className="text-muted text-[10px] mt-0.5 truncate max-w-[140px]">
                          {r.company_name}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <NumCell value={r.pe_ratio}
                        highlight={r.pe_ratio != null && r.pe_ratio < 25}
                        positiveGood={true} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <NumCell value={r.roe}
                        highlight={r.roe != null && r.roe > 15}
                        positiveGood={true} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <NumCell value={r.roce}
                        highlight={r.roce != null && r.roce > 15}
                        positiveGood={true} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <NumCell value={r.debt_to_equity}
                        highlight={r.debt_to_equity != null && r.debt_to_equity > 1}
                        positiveGood={false} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <NumCell value={r.promoter_holding}
                        highlight={r.promoter_holding != null && r.promoter_holding > 50}
                        positiveGood={true} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <NumCell value={r.pledged_pct}
                        highlight={r.pledged_pct != null && r.pledged_pct > 10}
                        positiveGood={false} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <ScoreCell score={r.fundamental_score} />
                    </td>
                    <td className="px-5 py-3 text-center">
                      <SignalBadge signal={scoreSignal(r.fundamental_score)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-5 text-[10px] text-muted">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-sm bg-profit/50 inline-block" />
          Score ≥ 70 — BUY
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-sm bg-warn/50 inline-block" />
          Score 40–70 — HOLD
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-sm bg-loss/50 inline-block" />
          Score &lt; 40 — SELL
        </span>
        <span className="ml-auto">
          Green cells = meets quality threshold · Red cells = below threshold
        </span>
      </div>
    </div>
  );
}
