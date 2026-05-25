import { IndianRupee, TrendingUp, Activity, BarChart2, Zap } from 'lucide-react';
import MetricCard     from '../components/MetricCard';
import CandlestickChart from '../components/CandlestickChart';
import OpenPositions  from '../components/OpenPositions';
import SignalBadge    from '../components/SignalBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import { usePortfolio } from '../hooks/usePortfolio';
import { useSignals }   from '../hooks/useSignals';

export default function Dashboard() {
  const { portfolio, loading: pLoading } = usePortfolio();
  const { signals,   loading: sLoading } = useSignals();

  if (pLoading) return <LoadingSpinner />;

  const balance        = portfolio?.balance ?? 0;
  const realisedPnl    = portfolio?.realised_pnl ?? 0;
  const unrealisedPnl  = portfolio?.unrealised_pnl ?? 0;
  const totalPnl       = realisedPnl + unrealisedPnl;
  const roi            = portfolio?.roi_percent ?? 0;
  const winRate        = portfolio?.win_rate ?? 0;
  const totalTrades    = portfolio?.total_trades ?? 0;

  return (
    <div className="space-y-5 fade-in">
      {/* KPI row */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <MetricCard title="Portfolio Value" value={balance}
          subtitle="Current virtual balance" trend={roi} icon={IndianRupee} />
        <MetricCard title="Total P&L" value={totalPnl}
          subtitle="Realised + unrealised" trend={roi} icon={TrendingUp} />
        <MetricCard title="Win Rate" value={`${winRate.toFixed(1)}%`}
          subtitle="Closed profitable trades" trend={winRate - 50} icon={Activity} />
        <MetricCard title="Total Trades" value={totalTrades}
          subtitle="All time paper trades" icon={BarChart2} />
      </div>

      {/* Equity chart + Latest Signals */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <CandlestickChart />
        </div>

        {/* Signals panel */}
        <div className="rounded-xl border border-border flex flex-col overflow-hidden" style={{ background: '#0F1829' }}>
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
            <div className="flex items-center gap-2">
              <Zap size={14} className="text-cyan" />
              <h2 className="text-slate-100 font-semibold text-sm">Latest Signals</h2>
            </div>
            <span className="text-muted text-xs">{signals.length} total</span>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-2 divide-y divide-border">
            {sLoading ? (
              <LoadingSpinner message="Fetching signals…" />
            ) : signals.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-muted text-sm">No signals yet</p>
                <p className="text-muted/50 text-xs">Waiting for market scan…</p>
              </div>
            ) : (
              signals.slice(0, 10).map((s, i) => (
                <div key={i} className="flex items-center justify-between py-2.5">
                  <div>
                    <p className="text-slate-200 text-sm font-semibold">{s.symbol}</p>
                    {s.confidence != null && (
                      <p className="text-muted text-[10px] mt-0.5">
                        {s.pattern_name ?? s.timeframe} · {(s.confidence * (s.confidence > 1 ? 1 : 100)).toFixed(1)}% conf
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

      {/* Open Positions */}
      <OpenPositions positions={portfolio?.positions ?? []} />
    </div>
  );
}
