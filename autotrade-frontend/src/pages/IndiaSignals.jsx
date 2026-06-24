import { useState, useEffect, useCallback } from 'react';
import { Zap } from 'lucide-react';
import SignalBadge    from '../components/SignalBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import { getIndiaSignals } from '../api/client';

export default function IndiaSignals() {
  const [signals, setSignals] = useState([]);
  const [tab,     setTab]     = useState('stocks');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (cat) => {
    setLoading(true);
    try {
      const data = await getIndiaSignals(cat);
      setSignals(Array.isArray(data) ? data : data?.signals ?? []);
    } catch {
      setSignals([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(tab);
    const id = setInterval(() => load(tab), 10000);
    return () => clearInterval(id);
  }, [load, tab]);

  return (
    <div className="space-y-5 fade-in">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap size={18} className="text-cyan" />
          <h1 className="text-slate-100 font-bold text-lg">NSE Signals</h1>
        </div>
        {!loading && <span className="text-muted text-sm">{signals.length} signals</span>}
      </div>

      <div className="flex gap-2">
        {['stocks', 'indices', 'forex'].map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm font-semibold capitalize transition-colors ${
              tab === t
                ? 'bg-accent/20 text-accent border border-accent/30'
                : 'text-muted bg-surface border border-border hover:text-slate-300 hover:bg-white/5'
            }`}>
            {t}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-border overflow-hidden glass-panel">
        {loading ? (
          <LoadingSpinner />
        ) : signals.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2">
            <p className="text-muted text-sm">No signals for {tab}</p>
            <p className="text-muted/50 text-xs">Market may be closed or no scan has run yet</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted text-[10px] uppercase tracking-wider">
                  <th className="text-left px-5 py-3">Symbol</th>
                  <th className="text-left px-4 py-3">Timeframe</th>
                  <th className="text-left px-4 py-3">Pattern</th>
                  <th className="text-right px-4 py-3">Confidence</th>
                  <th className="text-right px-4 py-3">Score</th>
                  <th className="text-center px-4 py-3">Signal</th>
                  <th className="text-right px-5 py-3">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {signals.map((s, i) => (
                  <tr key={i} className="hover:bg-white/2 transition-colors">
                    <td className="px-5 py-3 font-semibold text-slate-200">
                      {s.symbol?.replace('.NS', '')}
                    </td>
                    <td className="px-4 py-3 text-muted text-xs">{s.timeframe ?? '—'}</td>
                    <td className="px-4 py-3 text-muted text-xs">{s.pattern_name ?? '—'}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {s.confidence != null
                        ? <span className="text-slate-300">{(+s.confidence).toFixed(1)}%</span>
                        : <span className="text-muted">—</span>}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {s.final_score != null
                        ? <span className="text-slate-300">{(+s.final_score).toFixed(1)}</span>
                        : <span className="text-muted">—</span>}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <SignalBadge signal={s} />
                    </td>
                    <td className="px-5 py-3 text-right text-muted text-xs">
                      {s.created_at
                        ? new Date(s.created_at).toLocaleTimeString('en-US', {
                            hour: '2-digit', minute: '2-digit',
                          })
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
