import { Clock, ShieldAlert, Target, ArrowUpRight } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import DirectionBadge from './DirectionBadge';
import PnLPct from './PnLPct';
import { fmt, fmtQty, elapsed } from '../../utils/tradeFormat';

/**
 * Single open-position card. Extracted verbatim (same markup/math) from the
 * original inline OpenPositionsSection loop in pages/Trades.jsx, so this is
 * a structural refactor, not a behavior change.
 *
 * `sparklineSlot` is an optional ReactNode injection point for Phase 4's
 * PositionSparkline, so wiring it in later doesn't require touching this
 * component's layout again.
 */
export default function PositionCard({ position: pos, sparklineSlot = null, index = 0, onSelect }) {
  const prefersReducedMotion = useReducedMotion();
  const pnl = pos.unrealised_pnl ?? 0;
  const pct = pos.unrealised_pct ?? 0;
  const isBuy = pos.direction?.toUpperCase() === 'BUY';
  /* For BUY:  size_usd + pnl = qty×entry + qty×(cur−entry) = qty×cur  ✓
     For SELL: size_usd − pnl = qty×entry − qty×(entry−cur) = qty×cur  ✓ */
  const currentVal = (pos.size_usd ?? 0) + (isBuy ? pnl : -pnl);
  const isProfit = pnl >= 0;
  const priceMove = pos.current_price - pos.entry_price;

  const slDist = pos.stop_loss
    ? Math.abs((pos.current_price - pos.stop_loss) / pos.current_price * 100)
    : null;
  const tpDist = pos.take_profit
    ? Math.abs((pos.take_profit - pos.current_price) / pos.current_price * 100)
    : null;

  return (
    <motion.div
      layout={!prefersReducedMotion}
      initial={prefersReducedMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.18, delay: Math.min(index, 12) * 0.015 }}
      tabIndex={0}
      data-position-nav="true"
      role="button"
      aria-label={`${pos.symbol} ${isBuy ? 'buy' : 'sell'} position, unrealised P&L ${isProfit ? 'gain' : 'loss'} of ${fmt(Math.abs(pnl))}. Press Enter for details.`}
      onClick={() => onSelect?.(pos)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect?.(pos);
        }
      }}
      className={`glass-panel rounded-xl p-5 space-y-3 cursor-pointer hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent transition-all duration-300 border ${
        isProfit ? 'border-profit/30 shadow-[0_0_15px_rgba(0,230,118,0.05)]' : 'border-loss/30 shadow-[0_0_15px_rgba(255,23,68,0.05)]'
      }`}
    >
      {/* Row 1: symbol + direction + timer */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-bold text-slate-100 text-base">{pos.symbol}</span>
          <DirectionBadge direction={pos.direction} />
        </div>
        <div className="flex items-center gap-1 text-muted text-[11px]">
          <Clock size={11} aria-hidden="true" />
          {elapsed(pos.opened_at)}
        </div>
      </div>

      {/* Row 2: Unrealised P&L hero */}
      <div className="flex items-end justify-between">
        <div>
          <p className="text-muted text-[10px] uppercase tracking-wide mb-0.5">Unrealised P&amp;L</p>
          <p className={`text-2xl font-extrabold tabular-nums ${isProfit ? 'text-profit' : 'text-loss'}`}>
            {isProfit ? '+' : ''}{fmt(pnl)}
          </p>
        </div>
        <PnLPct value={pct} />
      </div>

      {sparklineSlot}

      {/* Row 3: price line */}
      <div className="flex items-center justify-between text-xs">
        <div className="flex flex-col">
          <span className="text-muted text-[10px]">Entry</span>
          <span className="text-slate-300 tabular-nums font-medium">{fmt(pos.entry_price)}</span>
        </div>
        <div className={`flex items-center gap-1 text-xs font-bold ${priceMove >= 0 ? 'text-profit' : 'text-loss'}`}>
          {priceMove >= 0 ? '▲' : '▼'} {fmt(Math.abs(priceMove))}
        </div>
        <div className="flex flex-col items-end">
          <span className="text-muted text-[10px]">Current</span>
          <span className="text-slate-100 tabular-nums font-bold">{fmt(pos.current_price)}</span>
        </div>
      </div>

      {/* Row 4: capital invested → current value */}
      <div className="flex items-center justify-between bg-surface/50 rounded-lg px-3 py-2 text-xs">
        <div>
          <p className="text-muted text-[10px]">Qty / Invested</p>
          <p className="text-slate-200 tabular-nums font-semibold">{fmtQty(pos.size_units)} shares</p>
          <p className="text-muted text-[9px] mt-0.5">{fmt(pos.size_usd)} @ {fmt(pos.entry_price)}/sh</p>
        </div>
        <ArrowUpRight size={14} className="text-muted" aria-hidden="true" />
        <div className="text-right">
          <p className="text-muted text-[10px]">Current Value</p>
          <p className={`tabular-nums font-semibold ${isProfit ? 'text-profit' : 'text-loss'}`}>
            {fmt(currentVal)}
          </p>
        </div>
      </div>

      {/* Row 5: SL / TP */}
      <div className="flex items-center justify-between text-[11px]">
        <div className="flex items-center gap-1 text-loss">
          <ShieldAlert size={11} aria-hidden="true" />
          <span className="text-muted">SL</span>
          <span className="tabular-nums font-medium">{pos.stop_loss ? fmt(pos.stop_loss) : '—'}</span>
          {slDist != null && (
            <span className="text-muted">({slDist.toFixed(1)}% away)</span>
          )}
        </div>
        <div className="flex items-center gap-1 text-profit">
          <Target size={11} aria-hidden="true" />
          <span className="text-muted">TP</span>
          <span className="tabular-nums font-medium">{pos.take_profit ? fmt(pos.take_profit) : '—'}</span>
          {tpDist != null && (
            <span className="text-muted">({tpDist.toFixed(1)}% away)</span>
          )}
        </div>
      </div>
    </motion.div>
  );
}
