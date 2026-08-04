import { useEffect, useRef, useState } from 'react';
import { motion, animate, useReducedMotion } from 'framer-motion';

/**
 * Animated stat card: tweens the displayed number from its previous value
 * to the new one whenever `numericValue` changes (e.g. a WebSocket-driven
 * P&L update), rather than just snapping the text. `formatValue` receives
 * the in-flight tweened number every animation frame and must return the
 * final display string (so currency formatting/sign/decimals stay correct
 * mid-animation, not just at the end).
 */
export default function StatCard({ label, numericValue, formatValue, sub, icon: Icon, color, bg, index = 0 }) {
  const prefersReducedMotion = useReducedMotion();
  const [display, setDisplay] = useState(() => formatValue(numericValue));
  const prevValueRef = useRef(numericValue);
  const [flash, setFlash] = useState(null); // 'up' | 'down' | null

  useEffect(() => {
    // Reduced-motion users get the value directly from props during render
    // (see `shownValue` below) -- nothing to tween, so this effect has
    // nothing to do and must not call setState synchronously for that case.
    if (prefersReducedMotion) return;

    const from = prevValueRef.current;
    const to = numericValue;
    if (from === to) return;

    setFlash(to > from ? 'up' : 'down');
    // Matches the 1s duration of the .flash-green/.flash-red CSS animation
    // (index.css) -- clearing the class before it finishes would cut the
    // fade-out short on re-render.
    const flashTimer = setTimeout(() => setFlash(null), 1000);

    const controls = animate(from, to, {
      duration: 0.5,
      ease: 'easeOut',
      onUpdate: (v) => setDisplay(formatValue(v)),
    });
    prevValueRef.current = to;

    return () => {
      controls.stop();
      clearTimeout(flashTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numericValue, prefersReducedMotion]);

  const shownValue = prefersReducedMotion ? formatValue(numericValue) : display;

  return (
    <motion.div
      initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: prefersReducedMotion ? 0 : index * 0.05, ease: 'easeOut' }}
      className={`glass-panel rounded-xl p-5 min-w-0 hover:-translate-y-1 hover:shadow-[0_8px_30px_rgba(0,0,0,0.4)] transition-all duration-300 group relative overflow-hidden ${
        flash === 'up' ? 'flash-green' : flash === 'down' ? 'flash-red' : ''
      }`}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-muted text-xs font-medium">{label}</span>
        <span className={`w-8 h-8 rounded-lg ${bg} flex items-center justify-center shrink-0`}>
          <Icon size={15} className={color} aria-hidden="true" />
        </span>
      </div>
      <p className={`text-xl font-bold ${color} tabular-nums`} aria-live="off">{shownValue}</p>
      <p className="text-muted text-xs mt-1 truncate" title={sub}>{sub}</p>
    </motion.div>
  );
}
