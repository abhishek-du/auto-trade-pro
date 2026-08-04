import { useId, useMemo } from 'react';
import { motion, useReducedMotion } from 'framer-motion';

/**
 * Compact equity-curve sparkline for the summary section.
 *
 * Data source: GET /api/v1/portfolio/snapshots -- the only equity-history
 * endpoint that actually exists on the backend. It returns up to 30 DAILY
 * snapshots (one per calendar day), not intraday ticks -- there is no
 * intraday equity-history endpoint in this codebase today. This renders
 * the real daily trend rather than fabricating intraday granularity;
 * flagged in the phase summary as a correction to the original "intraday
 * performance" brief.
 *
 * Follows the app's existing hand-rolled <svg><polyline> sparkline pattern
 * (see pages/FundDetail.jsx's NavSparkline) instead of mounting a recharts
 * instance, consistent with the rest of the codebase for this kind of
 * lightweight inline chart.
 */
export default function EquitySparkline({ snapshots, height = 40, className = '' }) {
  const prefersReducedMotion = useReducedMotion();
  const gradientId = useId();

  const { points, areaPoints, isUp, hasData } = useMemo(() => {
    const equities = (snapshots || [])
      .map((s) => Number(s.equity))
      .filter((v) => Number.isFinite(v));
    if (equities.length < 2) return { points: '', areaPoints: '', isUp: true, hasData: false };

    const W = 300;
    const H = height;
    const min = Math.min(...equities);
    const max = Math.max(...equities);
    const range = max - min || 1;
    const coords = equities.map((v, i) => [
      (i / (equities.length - 1)) * W,
      H - ((v - min) / range) * (H - 4) - 2,
    ]);
    const line = coords.map(([x, y]) => `${x},${y}`).join(' ');
    const area = `0,${H} ${line} ${W},${H}`;
    return {
      points: line,
      areaPoints: area,
      isUp: equities[equities.length - 1] >= equities[0],
      hasData: true,
    };
  }, [snapshots, height]);

  if (!hasData) {
    return (
      <div
        className={`flex items-center justify-center text-[11px] text-muted ${className}`}
        style={{ height }}
      >
        Not enough history yet
      </div>
    );
  }

  const strokeColor = isUp ? 'var(--color-profit)' : 'var(--color-loss)';

  return (
    <svg
      viewBox={`0 0 300 ${height}`}
      className={className}
      style={{ width: '100%', height }}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Equity trend over the last ${(snapshots || []).length} trading days, ${isUp ? 'up' : 'down'} overall`}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.25" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      <motion.polygon
        points={areaPoints}
        fill={`url(#${gradientId})`}
        initial={prefersReducedMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.3 }}
      />
      <motion.polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={prefersReducedMotion ? false : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.6, ease: 'easeOut' }}
      />
    </svg>
  );
}
