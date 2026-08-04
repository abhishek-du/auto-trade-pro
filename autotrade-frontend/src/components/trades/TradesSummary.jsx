import { useEffect, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Wallet, BarChart2, ArrowUpRight, ArrowDownRight, TrendingUp, TrendingDown } from 'lucide-react';
import StatCard from './StatCard';
import EquitySparkline from './EquitySparkline';
import { getPortfolioSnapshots } from '../../api/client';
import { formatINR } from '../../utils/indianFormat';

const fmt = (n, dec = 2) => formatINR(n ?? 0, dec);

/**
 * Redesigned 4-card summary banner + equity sparkline.
 *
 * The gain/loss math below is copied verbatim from the original
 * InvestmentSummary (pages/Trades.jsx) -- every `??` fallback is load-
 * bearing per that component's own inline comments (live position P&L can
 * outrun a lagging agentPortfolio snapshot; equity always prefers the live
 * API value over any hardcoded starting constant). Not simplified here to
 * avoid silently changing what these numbers mean.
 */
export default function TradesSummary({ wallet, agentStatus, trades = [], positions = [] }) {
  const prefersReducedMotion = useReducedMotion();
  const [snapshots, setSnapshots] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await getPortfolioSnapshots();
        if (!cancelled) setSnapshots(Array.isArray(data) ? data : []);
      } catch {
        if (!cancelled) setSnapshots([]);
      }
    }
    load();
    const id = setInterval(load, 60_000); // equity history changes at most once/day; 60s is plenty
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const agentPortfolio = agentStatus?.portfolio ?? null;
  const realisedPnl = agentPortfolio?.realised_pnl ?? wallet?.realised_pnl ?? 0;
  const liveUnrealisedPnl = positions.reduce((s, p) => s + (p.unrealised_pnl ?? 0), 0);
  const unrealisedPnl = liveUnrealisedPnl || (agentPortfolio?.unrealised_pnl ?? wallet?.unrealised_pnl ?? 0);
  const totalPnl = realisedPnl + unrealisedPnl;
  const portfolioValue = agentPortfolio?.equity ?? wallet?.equity ?? 100_000;
  // `portfolioValue - totalPnl` is always a number (never nullish), so a
  // trailing `?? 100_000` here can never fire -- confirmed the same dead
  // fallback exists in the original InvestmentSummary (Trades.jsx:465,
  // same eslint no-constant-binary-expression warning there). Removed
  // rather than carried forward; behavior is identical since it was
  // unreachable.
  const START_CAPITAL = agentPortfolio?.start_capital ?? wallet?.peak_balance ?? (portfolioValue - totalPnl);
  const agentCash = agentPortfolio?.cash ?? null;
  const roiPct = START_CAPITAL > 0 ? ((portfolioValue - START_CAPITAL) / START_CAPITAL) * 100 : 0;
  const isGain = totalPnl >= 0;

  const openTrades = trades.filter((t) => (t.status ?? 'CLOSED').toUpperCase() === 'OPEN');

  const cards = [
    {
      label: 'Agent Equity',
      numericValue: portfolioValue,
      formatValue: (v) => fmt(v),
      sub: agentCash !== null
        ? `Free cash: ${fmt(agentCash)} · ${openTrades.length} open`
        : `${openTrades.length} AI positions open`,
      icon: Wallet,
      color: 'text-cyan',
      bg: 'bg-cyan/10',
    },
    {
      label: 'Portfolio Value',
      numericValue: portfolioValue,
      formatValue: (v) => fmt(v),
      sub: `${fmt(START_CAPITAL)} starting · ${unrealisedPnl >= 0 ? '+' : ''}${fmt(unrealisedPnl)} unrealised`,
      icon: BarChart2,
      color: 'text-blue-400',
      bg: 'bg-blue-500/10',
    },
    {
      label: 'Total P&L',
      numericValue: totalPnl,
      formatValue: (v) => (v >= 0 ? '+' : '') + fmt(v),
      sub: `Realised ${fmt(realisedPnl)}  ·  Unrealised ${unrealisedPnl >= 0 ? '+' : ''}${fmt(unrealisedPnl)}`,
      icon: isGain ? ArrowUpRight : ArrowDownRight,
      color: isGain ? 'text-profit' : 'text-loss',
      bg: isGain ? 'bg-profit/10' : 'bg-loss/10',
    },
    {
      label: 'Return on Investment',
      numericValue: roiPct,
      formatValue: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
      sub: `Net P&L ${isGain ? '+' : ''}${fmt(totalPnl)} on ${fmt(START_CAPITAL)} capital`,
      icon: roiPct >= 0 ? TrendingUp : TrendingDown,
      color: roiPct >= 0 ? 'text-profit' : 'text-loss',
      bg: roiPct >= 0 ? 'bg-profit/10' : 'bg-loss/10',
    },
  ];

  return (
    <div className="space-y-3">
      {/* Mobile (<640px): horizontal scroll-snap carousel. sm+: responsive grid. */}
      <div
        className="
          flex gap-4 overflow-x-auto snap-x snap-mandatory pb-1 -mx-1 px-1
          sm:grid sm:grid-cols-2 xl:grid-cols-4 sm:overflow-visible sm:pb-0 sm:mx-0 sm:px-0
        "
      >
        {cards.map((card, i) => (
          <div key={card.label} className="snap-center shrink-0 w-[82%] xs:w-[70%] sm:w-auto sm:shrink">
            <StatCard {...card} index={i} />
          </div>
        ))}
      </div>

      <motion.div
        initial={prefersReducedMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2, delay: 0.15 }}
        className="glass-panel rounded-xl p-4"
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-muted text-xs font-medium">Equity trend (last {snapshots?.length ?? 0} trading days)</span>
        </div>
        {snapshots === null ? (
          <div className="shimmer rounded h-10 w-full" />
        ) : (
          <EquitySparkline snapshots={snapshots} height={40} />
        )}
      </motion.div>
    </div>
  );
}
