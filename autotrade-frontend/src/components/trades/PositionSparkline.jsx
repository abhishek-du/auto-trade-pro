import { useMemo } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { useTickBuffer } from '../../hooks/trades/usePositionSparklineBuffer';

/**
 * Inline per-position sparkline built from live-tick buffering (see
 * usePositionSparklineBuffer) -- NOT historical data, just what's been
 * observed over the WebSocket since this card mounted. Colored green/red
 * based on the buffered trend (first vs. last buffered tick), same
 * hand-rolled <svg><polyline> approach as EquitySparkline/FundDetail's
 * NavSparkline rather than mounting a recharts instance per card (there
 * can be 80+ of these on screen at once).
 */
export default function PositionSparkline({ currentPrice, height = 28 }) {
  const prefersReducedMotion = useReducedMotion();
  const buffer = useTickBuffer(currentPrice);

  const { points, isUp, hasEnough } = useMemo(() => {
    if (buffer.length < 2) return { points: '', isUp: true, hasEnough: false };
    const W = 200;
    const H = height;
    const min = Math.min(...buffer);
    const max = Math.max(...buffer);
    const range = max - min || 1;
    const coords = buffer.map((v, i) => [
      (i / (buffer.length - 1)) * W,
      H - ((v - min) / range) * (H - 4) - 2,
    ]);
    return {
      points: coords.map(([x, y]) => `${x},${y}`).join(' '),
      isUp: buffer[buffer.length - 1] >= buffer[0],
      hasEnough: true,
    };
  }, [buffer, height]);

  if (!hasEnough) {
    return (
      <div
        className="flex items-center text-[10px] text-muted/60"
        style={{ height }}
        aria-hidden="true"
      >
        Watching live ticks…
      </div>
    );
  }

  const strokeColor = isUp ? 'var(--color-profit)' : 'var(--color-loss)';

  return (
    <svg
      viewBox={`0 0 200 ${height}`}
      style={{ width: '100%', height }}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Recent live price trend, ${isUp ? 'up' : 'down'}, ${buffer.length} ticks observed`}
    >
      <motion.polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={prefersReducedMotion ? false : { pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
      />
    </svg>
  );
}
