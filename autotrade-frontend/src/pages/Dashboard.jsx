import { DollarSign, TrendingUp, Activity, BarChart2 } from 'lucide-react';
import MetricCard from '../components/MetricCard';
import CandlestickChart from '../components/CandlestickChart';
import OpenPositions from '../components/OpenPositions';
import SignalBadge from '../components/SignalBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import { usePortfolio } from '../hooks/usePortfolio';
import { useSignals } from '../hooks/useSignals';

export default function Dashboard() {
  const { portfolio, loading: pLoading } = usePortfolio();
  const { signals, loading: sLoading }   = useSignals();

  if (pLoading) return <LoadingSpinner />;

  const balance   = portfolio?.balance ?? portfolio?.total_value ?? 0;
  const dailyPnl  = portfolio?.daily_pnl ?? 0;
  const dailyPct  = portfolio?.daily_pnl_pct ?? 0;
  const totalPnl  = portfolio?.total_pnl ?? 0;
  const totalPct  = portfolio?.total_pnl_pct ?? 0;
  const winRate   = portfolio?.win_rate ?? 0;

  return (
    <div className="space-y-6">
      {/* KPI row */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <MetricCard
          title="Portfolio Value"
          value={balance}
          subtitle="Total simulated balance"
          trend={dailyPct}
          icon={DollarSign}
        />
        <MetricCard
          title="Today's P&L"
          value={dailyPnl}
          subtitle="Unrealised + realised"
          trend={dailyPct}
          icon={TrendingUp}
        />
        <MetricCard
          title="Total P&L"
          value={totalPnl}
          subtitle="Since simulation start"
          trend={totalPct}
          icon={BarChart2}
        />
        <MetricCard
          title="Win Rate"
          value={`${winRate.toFixed(1)}%`}
          subtitle="Closed profitable trades"
          trend={winRate - 50}
          icon={Activity}
        />
      </div>

      {/* Chart + Signals */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <CandlestickChart symbol="BTC/USD" />
        </div>
        <div className="bg-panel border border-border rounded-xl p-4 space-y-3">
          <h2 className="text-slate-100 font-semibold text-sm">Latest Signals</h2>
          {sLoading
            ? <LoadingSpinner message="Fetching signals…" />
            : signals.slice(0, 8).map((s, i) => (
                <div key={i} className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
                  <span className="text-slate-300 text-sm font-medium">{s.symbol ?? s.ticker ?? '—'}</span>
                  <SignalBadge signal={s} />
                </div>
              ))
          }
        </div>
      </div>

      {/* Open Positions */}
      <OpenPositions positions={portfolio?.positions ?? []} />
    </div>
  );
}
