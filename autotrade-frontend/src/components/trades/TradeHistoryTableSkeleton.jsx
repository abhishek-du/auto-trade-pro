const COLS = 11;

/**
 * Suspense fallback for the lazy-loaded TradeHistoryTable chunk. Shaped like
 * the real table (filter bar + header + pulsing rows) rather than a generic
 * spinner, so the layout doesn't jump once the chunk resolves.
 */
export default function TradeHistoryTableSkeleton() {
  return (
    <div className="glass-panel rounded-xl overflow-hidden animate-pulse" aria-hidden="true">
      <div className="p-4 border-b border-border flex flex-wrap items-center gap-3">
        <div className="h-9 flex-1 min-w-40 bg-surface rounded-lg" />
        <div className="h-9 w-40 bg-surface rounded-lg" />
        <div className="h-9 w-48 bg-surface rounded-lg" />
        <div className="h-9 w-40 bg-surface rounded-lg" />
      </div>
      <div className="p-4 space-y-2">
        <div className="h-8 bg-surface/60 rounded" />
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-12 bg-surface/40 rounded" style={{ gridTemplateColumns: `repeat(${COLS}, 1fr)` }} />
        ))}
      </div>
    </div>
  );
}
