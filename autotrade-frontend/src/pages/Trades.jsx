import { useState, useEffect, useMemo, useCallback, lazy, Suspense } from 'react';
import { TrendingUp, IndianRupee, Activity, Bot } from 'lucide-react';
import { useTrades } from '../hooks/useTrades';
import TradesSummary from '../components/trades/TradesSummary';
import PositionsSection from '../components/trades/PositionsSection';
import TradeHistoryTableSkeleton from '../components/trades/TradeHistoryTableSkeleton';
import { TradesPreferencesProvider } from '../contexts/TradesPreferencesContext';
import { fmt } from '../utils/tradeFormat';
import { useWebSocket } from '../hooks/useWebSocket';
import { useTradeEventToasts } from '../hooks/trades/useTradeEventToasts';
import { useLivePrices } from '../contexts/LivePricesContext';
import { getPortfolio, getPortfolioPositions } from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';

const TradeHistoryTable = lazy(() => import('../components/trades/TradeHistoryTable'));

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function Trades() {
  return (
    <TradesPreferencesProvider>
      <TradesInner />
    </TradesPreferencesProvider>
  );
}

function TradesInner() {
  const { trades, loading, error: tradesError, refetch: refetchTrades } = useTrades();
  const [wallet,        setWallet]        = useState(null);
  const [positions,     setPositions]     = useState([]);
  const [agentStatus,   setAgentStatus]   = useState(null);
  const [agentActivity, setAgentActivity] = useState(null); // last agent event
  const { prices: livePrices, connected, lastAgentEvent } = useLivePrices();
  useTradeEventToasts();

  /* ── HTTP fallback — refresh every 30 s (WebSocket is primary) ── */
  useEffect(() => {
    function refresh() {
      getPortfolio().then(setWallet).catch(() => {});
      getPortfolioPositions().then(setPositions).catch(() => {});
      fetch('/api/v1/agent/status').then(r => r.ok ? r.json() : null).then(d => d && setAgentStatus(d)).catch(() => {});
    }
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, []);

  /* ── /ws/portfolio — wallet pushed every 10 s ── */
  const onPortfolioMsg = useCallback((msg) => {
    if (msg.type === 'portfolio_update') {
      setWallet(prev => prev ? {
        ...prev,
        balance:        msg.balance,
        equity:         msg.equity,
        unrealised_pnl: msg.unrealised_pnl,
        realised_pnl:   msg.realised_pnl,
        roi_percent:    msg.roi_percent,
      } : prev);
    }
  }, []);
  useWebSocket('/ws/portfolio', { onMessage: onPortfolioMsg });

  /* ── agent events from shared WebSocket context ── */
  useEffect(() => {
    if (!lastAgentEvent) return;
    setAgentActivity(lastAgentEvent);
    if (lastAgentEvent.event === 'TRADE_OPENED' || lastAgentEvent.event === 'TRADE_CLOSED') {
      refetchTrades();
      getPortfolioPositions().then(setPositions).catch(() => {});
      getPortfolio().then(setWallet).catch(() => {});
    }
  }, [lastAgentEvent, refetchTrades]);
  const wsStatus = connected ? 'connected' : 'closed';

  /* build symbol → position map for fast lookup.
     Agent trades use id="agent_N" which never matches OpenPosition.trade_id
     (which links to PaperTrade). Symbol lookup works for both sources. */
  const positionBySymbol = useMemo(() => {
    const m = {};
    positions.forEach((p) => {
      const sym = (p.symbol ?? '').replace('.NS', '').toUpperCase();
      if (sym) m[sym] = p;
    });
    return m;
  }, [positions]);

  // Any exited trade counts as "closed" for stats purposes -- CLOSED (target/
  // reversal exit) AND STOPPED (stop-loss exit) are both terminal states, only
  // OPEN isn't. Matching just 'CLOSED' silently dropped every stop-loss exit
  // (almost always a loss) from win-rate/best/worst, inflating the shown win
  // rate (44.4% vs the real 15.4% on 2026-07-28's data, where 7 of 13 exited
  // trades were STOPPED and excluded). NOTE: this fix was silently reverted
  // once already (file overwritten externally between edits) -- if this
  // comment is ever missing again while the bug is back, check what's
  // rewriting this file outside normal edits.
  const closed     = trades.filter((t) => (t.status ?? 'CLOSED').toUpperCase() !== 'OPEN');
  // For open trades, use live unrealised P&L from position map (or trade record)
  const openTrades = trades.filter((t) => (t.status ?? 'CLOSED').toUpperCase() === 'OPEN');
  const openPnls   = openTrades.map((t) => {
    const tradeSym = (t.symbol ?? t.ticker ?? '').replace('.NS', '').toUpperCase();
    const pos      = positionBySymbol[tradeSym] ?? null;
    return pos?.unrealised_pnl ?? t.pnl ?? 0;
  });
  const allPnls    = [
    ...closed.map((t) => t.pnl ?? 0),
    ...openPnls,
  ];
  const wins       = closed.filter((t) => (t.pnl ?? 0) > 0);
  const openWins   = openPnls.filter((p) => p > 0);
  const totalWins  = wins.length + openWins.length;
  const totalTrades = allPnls.length;
  const winRate    = totalTrades ? (totalWins / totalTrades) * 100 : 0;
  const bestTrade  = allPnls.length ? Math.max(...allPnls) : 0;
  const worstTrade = allPnls.length ? Math.min(...allPnls) : 0;

  if (loading) return <LoadingSpinner />;

  return (
    <div className="space-y-6">

      {/* ── Investment summary ── */}
      <TradesSummary wallet={wallet} agentStatus={agentStatus} trades={trades} positions={positions} />

      {/* ── WebSocket status + agent activity ── */}
      <div className="flex items-center gap-3 text-[11px]">
        <span className={`flex items-center gap-1 ${wsStatus === 'connected' ? 'text-profit' : 'text-muted'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${wsStatus === 'connected' ? 'bg-profit animate-pulse' : 'bg-slate-500'}`} />
          {wsStatus === 'connected' ? 'Live WebSocket' : 'Reconnecting…'}
        </span>
        {agentActivity && (
          <span className="flex items-center gap-1 text-cyan-400">
            <Bot size={11} />
            Agent: {agentActivity.event} {agentActivity.symbol ?? ''} {agentActivity.pnl != null ? `₹${agentActivity.pnl?.toFixed(0)}` : ''}
          </span>
        )}
      </div>

      {/* ── Open positions (live) ── */}
      <PositionsSection positions={positions} livePrices={livePrices} trades={trades} />

      {/* ── Secondary stats row ── */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="glass-panel rounded-xl p-5 flex items-center gap-4 hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] transition-all duration-300">
          <Activity size={20} className="text-muted shrink-0" />
          <div>
            <p className="text-muted text-xs uppercase tracking-wider font-semibold">Total Trades</p>
            <p className="text-slate-100 font-bold text-xl">{trades.length}</p>
          </div>
        </div>
        <div className="glass-panel rounded-xl p-5 flex items-center gap-4 hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] transition-all duration-300">
          <TrendingUp size={20} className={winRate >= 50 ? 'text-profit' : 'text-muted'} />
          <div>
            <p className="text-muted text-xs uppercase tracking-wider font-semibold">Win Rate</p>
            <p className={`font-bold text-xl ${winRate >= 50 ? 'text-profit' : 'text-loss'}`}>
              {winRate.toFixed(1)}%
            </p>
            {/* Must match winRate's own counts (totalWins/totalTrades), not just
                closed trades -- winRate already folds in currently-profitable
                open positions as "wins", so a closed-only subtitle here would
                silently disagree with the headline percentage above it. */}
            <p className="text-muted text-[10px]">{totalWins}W / {totalTrades - totalWins}L <span className="opacity-60">(incl. open)</span></p>
          </div>
        </div>
        <div className="glass-panel rounded-xl p-5 flex items-center gap-4 hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] transition-all duration-300">
          <IndianRupee size={20} className="text-profit shrink-0" />
          <div>
            <p className="text-muted text-xs uppercase tracking-wider font-semibold">Best Trade</p>
            <p className="text-profit font-bold text-xl">{fmt(bestTrade)}</p>
          </div>
        </div>
        <div className="glass-panel rounded-xl p-5 flex items-center gap-4 hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] transition-all duration-300">
          <IndianRupee size={20} className="text-loss shrink-0" />
          <div>
            <p className="text-muted text-xs uppercase tracking-wider font-semibold">Worst Trade</p>
            <p className="text-loss font-bold text-xl">{fmt(worstTrade)}</p>
          </div>
        </div>
      </div>

      {/* ── Trade history ── */}
      <Suspense fallback={<TradeHistoryTableSkeleton />}>
        <TradeHistoryTable trades={trades} positionBySymbol={positionBySymbol} loading={loading} error={tradesError} />
      </Suspense>
    </div>
  );
}
