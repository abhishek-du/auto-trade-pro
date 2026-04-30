/**
 * GoLiveChecker — maps the backend ShouldGoLiveOut response:
 * {
 *   ready: bool, reason: str,
 *   metrics: {
 *     win_rate, min_win_rate, roi_percent, min_roi,
 *     total_trades, min_trades, max_drawdown_pct, max_drawdown_limit,
 *     checks: { win_rate_ok, roi_ok, trades_ok, drawdown_ok }
 *   }
 * }
 */
import { useState, useCallback } from 'react';
import { CheckCircle2, XCircle, RefreshCw, AlertTriangle, TrendingUp } from 'lucide-react';
import LoadingSpinner from './LoadingSpinner';
import { getGoLiveStatus } from '../api/client';

function buildChecks(metrics) {
  if (!metrics) return [];
  const c = metrics.checks ?? {};
  return [
    {
      pass:    c.win_rate_ok ?? false,
      label:   'Win Rate > 55%',
      current: `${(metrics.win_rate ?? 0).toFixed(1)}%`,
      target:  `${metrics.min_win_rate ?? 55}%+`,
    },
    {
      pass:    c.trades_ok ?? false,
      label:   'Minimum 30 Trades Completed',
      current: `${metrics.total_trades ?? 0} trades`,
      target:  `${metrics.min_trades ?? 30} trades`,
    },
    {
      pass:    c.roi_ok ?? false,
      label:   'ROI > 10%',
      current: `${(metrics.roi_percent ?? 0).toFixed(2)}%`,
      target:  `${metrics.min_roi ?? 10}%+`,
    },
    {
      pass:    c.drawdown_ok ?? false,
      label:   'Max Drawdown < 20%',
      current: `${(metrics.max_drawdown_pct ?? 0).toFixed(2)}%`,
      target:  `< ${metrics.max_drawdown_limit ?? 20}%`,
    },
  ];
}

function CheckRow({ pass, label, current, target }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-border last:border-0">
      <div className="flex items-center gap-3">
        {pass
          ? <CheckCircle2 size={18} className="text-profit shrink-0" />
          : <XCircle      size={18} className="text-loss  shrink-0" />
        }
        <span className="text-slate-300 text-sm">{label}</span>
      </div>
      <div className="flex items-center gap-3 text-xs tabular-nums">
        <span className={pass ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
          {current}
        </span>
        <span className="text-muted">(need {target})</span>
      </div>
    </div>
  );
}

export default function GoLiveChecker() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getGoLiveStatus();
      setStatus(data);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, []);

  const allPass = status?.ready === true;
  const rows    = buildChecks(status?.metrics);

  return (
    <div className="space-y-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-slate-100 font-semibold text-base flex items-center gap-2">
          <TrendingUp size={18} className="text-accent" />
          Go-Live Readiness Checker
        </h2>
        <button
          onClick={fetchStatus}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-accent/20 hover:bg-accent/30 border border-accent/40 text-accent rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {fetched ? 'Check Again' : 'Check Now'}
        </button>
      </div>

      {loading && <LoadingSpinner message="Evaluating strategy performance…" />}

      {!loading && fetched && (
        <>
          {/* Checklist */}
          <div className="bg-surface border border-border rounded-xl px-5 divide-y divide-border">
            {rows.map((r) => <CheckRow key={r.label} {...r} />)}
          </div>

          {/* Verdict */}
          {allPass ? (
            <div className="bg-profit/10 border border-profit/30 rounded-xl p-5 flex items-start gap-3">
              <CheckCircle2 size={20} className="text-profit mt-0.5 shrink-0" />
              <div>
                <p className="text-profit font-bold text-sm">
                  Strategy is performing well — consider going live
                </p>
                <p className="text-profit/70 text-xs mt-1">
                  All criteria are met. Review the disclaimer below before making any decision.
                </p>
              </div>
            </div>
          ) : (
            <div className="bg-warn/10 border border-warn/30 rounded-xl p-5 flex items-start gap-3">
              <AlertTriangle size={20} className="text-warn mt-0.5 shrink-0" />
              <div>
                <p className="text-warn font-bold text-sm">
                  Keep simulating — requirements not yet met
                </p>
                <p className="text-warn/70 text-xs mt-1 leading-relaxed">
                  {status?.reason ?? 'One or more criteria not yet satisfied.'}
                </p>
              </div>
            </div>
          )}
        </>
      )}

      {!loading && !fetched && (
        <div className="bg-surface border border-border rounded-xl p-8 text-center text-muted text-sm">
          Press <span className="text-slate-300 font-medium">Check Now</span> to evaluate your strategy against go-live requirements.
        </div>
      )}

      {/* Permanent disclaimer */}
      <div className="bg-loss/10 border border-loss/30 rounded-xl p-5">
        <div className="flex items-start gap-3">
          <AlertTriangle size={18} className="text-loss mt-0.5 shrink-0" />
          <div className="space-y-1.5">
            <p className="text-loss font-bold text-sm uppercase tracking-wide">
              Important Risk Disclaimer
            </p>
            <p className="text-slate-400 text-xs leading-relaxed">
              Past paper trading performance does <strong className="text-slate-300">NOT</strong> guarantee future
              real results. Markets are unpredictable and conditions change constantly.
            </p>
            <p className="text-slate-400 text-xs leading-relaxed">
              Only invest money you can afford to lose entirely. Real trading involves additional costs not
              present in simulation: bid/ask spread, overnight financing fees, slippage, and liquidity gaps.
            </p>
            <p className="text-slate-400 text-xs leading-relaxed">
              <strong className="text-slate-300">Consult a qualified financial advisor</strong> before risking
              real capital. This tool is for educational purposes only — not financial advice.
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
