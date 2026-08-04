import { useState } from 'react';

const DEFAULT_MAX_POINTS = 30;

/**
 * Client-side ring buffer of a changing numeric value over time -- used to
 * build a "recent price action" sparkline from live WebSocket ticks, since
 * there's no per-symbol intraday tick-history endpoint on the backend (only
 * daily candles). Purely in-memory: the buffer starts empty on every page
 * load/remount and grows as live ticks arrive, not backed by any stored
 * history.
 *
 * Dedupes consecutive-identical values (a symbol with no WS activity would
 * otherwise fill the buffer with flat repeats of the same last-known price,
 * which is a meaningless "trend").
 *
 * Deliberately NOT a useEffect: this is "accumulate history as a prop
 * changes" (depends on the sequence of past values, not just the current
 * one), which React's own docs cover under "adjusting state when a prop
 * changes" / "storing information from previous renders" -- setState is
 * called conditionally during the render body itself (comparing against
 * the last-seen value), which React explicitly supports and immediately
 * re-renders with before committing, rather than an effect's extra
 * commit-then-run-effect-then-re-render round trip.
 */
export function useTickBuffer(value, maxPoints = DEFAULT_MAX_POINTS) {
  const [lastValue, setLastValue] = useState(value);
  const [buffer, setBuffer] = useState(() => (value == null || Number.isNaN(value) ? [] : [value]));

  if (value !== lastValue && value != null && !Number.isNaN(value)) {
    setLastValue(value);
    setBuffer((prev) => {
      const next = prev.length >= maxPoints ? prev.slice(1) : prev;
      return [...next, value];
    });
  }

  return buffer;
}
