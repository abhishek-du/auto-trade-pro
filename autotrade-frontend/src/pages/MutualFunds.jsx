import { useState, useEffect } from 'react';
import { Wallet, Calculator } from 'lucide-react';
import SignalBadge    from '../components/SignalBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import { getIndiaMutualFunds, projectSip } from '../api/client';

function fmtINR(n) {
  if (n == null) return '—';
  return `₹${(+n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function fmtPct(n, digits = 1) {
  if (n == null) return '—';
  const v = +n;
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`;
}

function PctCell({ value }) {
  if (value == null) return <span className="text-muted">—</span>;
  return (
    <span className={+value >= 0 ? 'text-profit' : 'text-loss'}>
      {fmtPct(value)}
    </span>
  );
}

export default function MutualFunds() {
  const [funds,       setFunds]       = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [calc, setCalc] = useState({
    schemeCode: '', monthlyAmount: 5000, months: 36, expectedReturn: 12,
  });
  const [result,      setResult]      = useState(null);
  const [calcLoading, setCalcLoading] = useState(false);
  const [calcError,   setCalcError]   = useState('');

  useEffect(() => {
    getIndiaMutualFunds()
      .then(data => {
        const list = data?.funds ?? [];
        setFunds(list);
        if (list.length > 0) {
          setCalc(prev => ({
            ...prev,
            schemeCode:     list[0].scheme_code,
            expectedReturn: list[0].one_yr_return ?? 12,
          }));
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleFundChange = (code) => {
    const fund = funds.find(f => f.scheme_code === code);
    setCalc(prev => ({
      ...prev,
      schemeCode:     code,
      expectedReturn: fund?.one_yr_return ?? prev.expectedReturn,
    }));
    setResult(null);
    setCalcError('');
  };

  const handleCalculate = async () => {
    setCalcLoading(true);
    setCalcError('');
    try {
      const res = await projectSip({
        monthly_amount:             +calc.monthlyAmount,
        expected_annual_return_pct: +calc.expectedReturn,
        months:                     +calc.months,
      });
      setResult(res);
    } catch (err) {
      setCalcError(err?.response?.data?.detail ?? 'Calculation failed');
    } finally {
      setCalcLoading(false);
    }
  };

  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-5 fade-in">

      {/* ── SIP Calculator ─────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border p-5 space-y-4" style={{ background: '#0F1829' }}>
        <div className="flex items-center gap-2">
          <Calculator size={16} className="text-cyan" />
          <h2 className="text-slate-100 font-semibold text-sm">SIP Calculator</h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <div className="space-y-1.5">
            <label className="text-muted text-[10px] uppercase tracking-widest">Fund</label>
            <select value={calc.schemeCode} onChange={e => handleFundChange(e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent">
              {funds.map(f => (
                <option key={f.scheme_code} value={f.scheme_code}>{f.name}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-muted text-[10px] uppercase tracking-widest">Monthly Amount (₹)</label>
            <input type="number" min="100" step="500" value={calc.monthlyAmount}
              onChange={e => setCalc(p => ({ ...p, monthlyAmount: e.target.value }))}
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent" />
          </div>
          <div className="space-y-1.5">
            <label className="text-muted text-[10px] uppercase tracking-widest">Duration (months)</label>
            <input type="number" min="1" max="360" value={calc.months}
              onChange={e => setCalc(p => ({ ...p, months: e.target.value }))}
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent" />
          </div>
          <div className="space-y-1.5">
            <label className="text-muted text-[10px] uppercase tracking-widest">Expected Return (%/yr)</label>
            <input type="number" min="0" max="50" step="0.5" value={calc.expectedReturn}
              onChange={e => setCalc(p => ({ ...p, expectedReturn: e.target.value }))}
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent" />
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button onClick={handleCalculate}
            disabled={calcLoading || !calc.schemeCode}
            className="px-6 py-2.5 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white rounded-lg text-sm font-semibold transition-colors">
            {calcLoading ? 'Calculating…' : 'Calculate'}
          </button>
          {calcError && <p className="text-loss text-xs">{calcError}</p>}
        </div>

        {result && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-4 border-t border-border">
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Total Invested</p>
              <p className="text-slate-100 font-bold text-xl tabular-nums">{fmtINR(result.total_invested)}</p>
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Projected Value</p>
              <p className="text-profit font-bold text-xl tabular-nums">{fmtINR(result.projected_value)}</p>
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Absolute Return</p>
              <p className="text-profit font-bold text-xl tabular-nums">{fmtINR(result.absolute_return)}</p>
            </div>
            <div>
              <p className="text-muted text-[10px] uppercase tracking-widest mb-1">Return %</p>
              <p className="text-accent font-bold text-xl tabular-nums">
                {result.absolute_return_pct != null
                  ? `+${(+result.absolute_return_pct).toFixed(1)}%`
                  : '—'}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* ── Fund table ─────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
        <div className="px-5 py-3.5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wallet size={14} className="text-cyan" />
            <h2 className="text-slate-100 font-semibold text-sm">Tracked Funds</h2>
          </div>
          <span className="text-muted text-xs">{funds.length} funds</span>
        </div>

        {funds.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2">
            <p className="text-muted text-sm">No fund data available</p>
            <p className="text-muted/50 text-xs">Run seed to fetch NAV data</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted text-[10px] uppercase tracking-wider">
                  <th className="text-left px-5 py-3">Fund Name</th>
                  <th className="text-left px-4 py-3">Category</th>
                  <th className="text-right px-4 py-3">NAV</th>
                  <th className="text-right px-4 py-3">1M Return</th>
                  <th className="text-right px-4 py-3">1Y Return</th>
                  <th className="text-right px-4 py-3">3Y Return</th>
                  <th className="text-center px-5 py-3">Signal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {funds.map((f, i) => (
                  <tr key={i}
                    onClick={() => handleFundChange(f.scheme_code)}
                    className={`hover:bg-white/2 transition-colors cursor-pointer ${
                      f.scheme_code === calc.schemeCode ? 'bg-accent/5' : ''
                    }`}>
                    <td className="px-5 py-3">
                      <p className="font-semibold text-slate-200 line-clamp-1">{f.name}</p>
                      <p className="text-muted text-[10px] mt-0.5">{f.scheme_code}</p>
                    </td>
                    <td className="px-4 py-3 text-muted text-xs">{f.category}</td>
                    <td className="px-4 py-3 text-right tabular-nums font-semibold text-slate-200">
                      ₹{(+f.nav).toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <PctCell value={f.one_month_return} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <PctCell value={f.one_yr_return} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <PctCell value={f.three_year_return} />
                    </td>
                    <td className="px-5 py-3 text-center">
                      <SignalBadge signal={{ signal_type: f.signal }} />
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
