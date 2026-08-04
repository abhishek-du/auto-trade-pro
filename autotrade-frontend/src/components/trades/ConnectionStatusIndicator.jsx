import { useEffect, useRef, useState } from 'react';
import { motion, useReducedMotion, AnimatePresence } from 'framer-motion';
import { WifiOff, RotateCw } from 'lucide-react';

/**
 * Live-connection pulse indicator + graceful reconnect/backoff UI.
 *
 * Phase 1: wraps LivePricesContext's `connected`/`lastUpdated` (the most
 * robust of the three WS connections feeding this page -- tiered backoff +
 * heartbeat + REST fallback already implemented there). Phase 5/7 extend
 * this to also reflect /ws/portfolio, /ws/positions-pnl, /ws/trades once
 * those get their own reconnect-state exposure.
 *
 * Visibly reacts to new data: the dot briefly scales/brightens on every
 * `lastUpdated` change (reuses the existing `.ws-live-dot` pulse-green
 * keyframe from index.css for the steady-state animation; the framer-motion
 * layer only handles the connect/disconnect/reconnecting state transitions).
 */
export default function ConnectionStatusIndicator({ connected, lastUpdated, reconnecting = false, compact = false }) {
  const prefersReducedMotion = useReducedMotion();
  const [justUpdated, setJustUpdated] = useState(false);
  const lastSeenRef = useRef(lastUpdated);

  useEffect(() => {
    if (lastUpdated && lastUpdated !== lastSeenRef.current) {
      lastSeenRef.current = lastUpdated;
      setJustUpdated(true);
      const t = setTimeout(() => setJustUpdated(false), 400);
      return () => clearTimeout(t);
    }
  }, [lastUpdated]);

  const label = reconnecting ? 'Reconnecting...' : connected ? 'Live' : 'Offline';

  return (
    <div
      className="flex items-center gap-1.5 text-xs font-medium select-none"
      role="status"
      aria-live="polite"
      aria-label={`Live data connection: ${label}`}
      title={label}
    >
      <AnimatePresence mode="wait" initial={false}>
        {reconnecting ? (
          <motion.span
            key="reconnecting"
            initial={prefersReducedMotion ? false : { opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={prefersReducedMotion ? undefined : { opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.18 }}
          >
            <RotateCw size={12} className="text-warn animate-spin" aria-hidden="true" />
          </motion.span>
        ) : connected ? (
          <motion.span
            key="connected"
            initial={prefersReducedMotion ? false : { opacity: 0, scale: 0.6 }}
            animate={{
              opacity: 1,
              scale: !prefersReducedMotion && justUpdated ? 1.35 : 1,
            }}
            exit={prefersReducedMotion ? undefined : { opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.18 }}
            className="ws-live-dot"
            aria-hidden="true"
          />
        ) : (
          <motion.span
            key="offline"
            initial={prefersReducedMotion ? false : { opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={prefersReducedMotion ? undefined : { opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.18 }}
          >
            <WifiOff size={12} className="text-loss" aria-hidden="true" />
          </motion.span>
        )}
      </AnimatePresence>
      {!compact && (
        <span className={connected ? 'text-muted' : 'text-loss'}>{label}</span>
      )}
    </div>
  );
}
