import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, Clock, ShieldAlert, Target, LineChart, Info } from 'lucide-react';
import DirectionBadge from './DirectionBadge';
import PnLPct from './PnLPct';
import { fmt, fmtQty, elapsed } from '../../utils/tradeFormat';

/**
 * Simulated-only SL/TP editor. Keyed by position.id at its usage site below
 * so React remounts a fresh instance (with fresh useState initializers)
 * whenever the selected position changes, instead of an effect resetting
 * state on prop change -- the React-team-recommended way to handle
 * "reset state when an id changes" (see "Resetting state with a key" /
 * "you might not need an effect" in the React docs) rather than a
 * setState-in-effect, which react-hooks/set-state-in-effect flags for good
 * reason (an extra render on every position switch).
 *
 * There is no backend endpoint to persist an SL/TP change on an open
 * position (confirmed: no PATCH/PUT route for this in api/portfolio.py or
 * api/trades.py), and the brief explicitly asked for "simulate only, no
 * real order routing" -- so this is intentionally local-only and discarded
 * when a different position is selected.
 */
function SimulatedSlTpForm({ position }) {
  const [simSL, setSimSL] = useState(() => (position.stop_loss != null ? String(position.stop_loss) : ''));
  const [simTP, setSimTP] = useState(() => (position.take_profit != null ? String(position.take_profit) : ''));
  const [dirty, setDirty] = useState(false);

  const simSLNum = parseFloat(simSL);
  const simTPNum = parseFloat(simTP);
  const simSlDist = Number.isFinite(simSLNum) && position.current_price
    ? Math.abs((position.current_price - simSLNum) / position.current_price * 100)
    : null;
  const simTpDist = Number.isFinite(simTPNum) && position.current_price
    ? Math.abs((simTPNum - position.current_price) / position.current_price * 100)
    : null;

  function handleFieldChange(setter) {
    return (e) => {
      setter(e.target.value);
      setDirty(true);
    };
  }

  function handleReset() {
    setSimSL(position.stop_loss != null ? String(position.stop_loss) : '');
    setSimTP(position.take_profit != null ? String(position.take_profit) : '');
    setDirty(false);
  }

  return (
    <div className="glass-panel rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-1.5 text-[10px] text-warn uppercase tracking-wide">
        <Info size={11} aria-hidden="true" />
        Simulated only — not sent to your broker
      </div>

      <label className="block">
        <span className="flex items-center gap-1 text-[11px] text-muted mb-1">
          <ShieldAlert size={11} className="text-loss" aria-hidden="true" /> Stop Loss
        </span>
        <input
          type="number"
          step="0.01"
          value={simSL}
          onChange={handleFieldChange(setSimSL)}
          className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-slate-100 tabular-nums focus:outline-none focus:border-accent"
        />
        {simSlDist != null && (
          <span className="text-[10px] text-muted mt-1 block">{simSlDist.toFixed(1)}% away from current price</span>
        )}
      </label>

      <label className="block">
        <span className="flex items-center gap-1 text-[11px] text-muted mb-1">
          <Target size={11} className="text-profit" aria-hidden="true" /> Take Profit
        </span>
        <input
          type="number"
          step="0.01"
          value={simTP}
          onChange={handleFieldChange(setSimTP)}
          className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-slate-100 tabular-nums focus:outline-none focus:border-accent"
        />
        {simTpDist != null && (
          <span className="text-[10px] text-muted mt-1 block">{simTpDist.toFixed(1)}% away from current price</span>
        )}
      </label>

      {dirty && (
        <button type="button" onClick={handleReset} className="text-[11px] text-accent hover:underline">
          Reset to actual SL/TP
        </button>
      )}
    </div>
  );
}

export default function PositionDetailDrawer({ position, onClose }) {
  const prefersReducedMotion = useReducedMotion();
  const panelRef = useRef(null);

  useEffect(() => {
    if (!position) return;
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    // Move focus into the panel for keyboard/screen-reader users.
    panelRef.current?.focus();
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [position, onClose]);

  const pos = position;
  const isBuy = pos?.direction?.toUpperCase() === 'BUY';
  const pnl = pos?.unrealised_pnl ?? 0;
  const isProfit = pnl >= 0;
  const currentVal = pos ? (pos.size_usd ?? 0) + (isBuy ? pnl : -pnl) : 0;

  return (
    <AnimatePresence>
      {pos && (
        <>
          <motion.div
            key="backdrop"
            className="fixed inset-0 bg-black/60 z-40"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.div
            key="panel"
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={`${pos.symbol} position details`}
            tabIndex={-1}
            initial={prefersReducedMotion ? { opacity: 0 } : { x: '100%' }}
            animate={prefersReducedMotion ? { opacity: 1 } : { x: 0 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { x: '100%' }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="fixed top-0 right-0 h-full w-full sm:w-[420px] bg-panel border-l border-border z-50 overflow-y-auto focus:outline-none"
          >
            {/* Header */}
            <div className="sticky top-0 bg-panel border-b border-border px-5 py-4 flex items-center justify-between z-10">
              <div className="flex items-center gap-2">
                <span className="font-bold text-lg text-slate-100">{pos.symbol}</span>
                <DirectionBadge direction={pos.direction} />
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close position details"
                className="p-1.5 rounded-lg text-muted hover:text-slate-200 hover:bg-white/[0.05] transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            <div className="p-5 space-y-5">
              {/* P&L hero */}
              <div className="glass-panel rounded-xl p-4">
                <p className="text-muted text-[10px] uppercase tracking-wide mb-1">Unrealised P&amp;L</p>
                <div className="flex items-end justify-between">
                  <p className={`text-3xl font-extrabold tabular-nums ${isProfit ? 'text-profit' : 'text-loss'}`}>
                    {isProfit ? '+' : ''}{fmt(pnl)}
                  </p>
                  <PnLPct value={pos.unrealised_pct} />
                </div>
                <div className="flex items-center gap-1 text-muted text-[11px] mt-2">
                  <Clock size={11} aria-hidden="true" />
                  Holding {elapsed(pos.opened_at)}
                </div>
              </div>

              {/* Price chart placeholder */}
              <div className="glass-panel rounded-xl p-4">
                <p className="text-muted text-[10px] uppercase tracking-wide mb-2">Price Chart</p>
                <div className="h-40 rounded-lg border border-dashed border-border flex flex-col items-center justify-center gap-2 text-muted">
                  <LineChart size={28} aria-hidden="true" />
                  <span className="text-xs">Full chart coming soon</span>
                </div>
              </div>

              {/* Position details grid */}
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="glass-panel rounded-lg p-3">
                  <p className="text-muted text-[10px]">Entry Price</p>
                  <p className="text-slate-200 font-semibold tabular-nums">{fmt(pos.entry_price)}</p>
                </div>
                <div className="glass-panel rounded-lg p-3">
                  <p className="text-muted text-[10px]">Current Price</p>
                  <p className="text-slate-100 font-bold tabular-nums">{fmt(pos.current_price)}</p>
                </div>
                <div className="glass-panel rounded-lg p-3">
                  <p className="text-muted text-[10px]">Quantity</p>
                  <p className="text-slate-200 font-semibold tabular-nums">{fmtQty(pos.size_units)} shares</p>
                </div>
                <div className="glass-panel rounded-lg p-3">
                  <p className="text-muted text-[10px]">Current Value</p>
                  <p className={`font-semibold tabular-nums ${isProfit ? 'text-profit' : 'text-loss'}`}>{fmt(currentVal)}</p>
                </div>
                <div className="glass-panel rounded-lg p-3 col-span-2">
                  <p className="text-muted text-[10px]">Source</p>
                  <p className="text-slate-200 font-medium">{pos.strategySource ?? 'Unknown'}</p>
                </div>
              </div>

              <SimulatedSlTpForm key={pos.id} position={pos} />
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
