import { useEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { AnimatePresence } from 'framer-motion';
import PositionCard from './PositionCard';

// Matches the original grid's Tailwind breakpoints (grid-cols-1 lg:grid-cols-2
// xl:grid-cols-3) -- replicated in JS because @tanstack/react-virtual has no
// built-in responsive multi-column grid mode; the standard way to virtualize
// a wrapping grid with it is to virtualize by ROW (each row = one band of N
// cards) rather than by individual card, chunking the flat array into rows
// based on measured container width.
const BREAKPOINTS = [
  { minWidth: 1280, columns: 3 }, // xl
  { minWidth: 1024, columns: 2 }, // lg
  { minWidth: 0, columns: 1 },
];

function useResponsiveColumns(containerRef) {
  const [columns, setColumns] = useState(1);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const compute = (width) => {
      const match = BREAKPOINTS.find((b) => width >= b.minWidth);
      setColumns(match ? match.columns : 1);
    };
    compute(el.clientWidth);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) compute(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [containerRef]);

  return columns;
}

const VIRTUALIZE_THRESHOLD = 30;

export default function PositionsGrid({ positions, renderSparkline, onSelectPosition }) {
  const containerRef = useRef(null);
  const columns = useResponsiveColumns(containerRef);

  const rows = useMemo(() => {
    const out = [];
    for (let i = 0; i < positions.length; i += columns) {
      out.push(positions.slice(i, i + columns));
    }
    return out;
  }, [positions, columns]);

  const shouldVirtualize = positions.length > VIRTUALIZE_THRESHOLD;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 280, // approximate card height incl. gap; re-measured per row below
    overscan: 3,
    enabled: shouldVirtualize,
  });

  if (positions.length === 0) return null;

  const gridColsClass = 'grid-cols-1 lg:grid-cols-2 xl:grid-cols-3';

  if (!shouldVirtualize) {
    // Small lists: render everything directly, no virtualization overhead --
    // AnimatePresence for add/remove transitions works cleanly this way too.
    return (
      <div ref={containerRef} className={`grid ${gridColsClass} gap-3`}>
        <AnimatePresence initial={false} mode="popLayout">
          {positions.map((pos, i) => (
            <PositionCard
              key={pos.id}
              position={pos}
              index={i}
              sparklineSlot={renderSparkline ? renderSparkline(pos) : null}
              onSelect={onSelectPosition}
            />
          ))}
        </AnimatePresence>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="overflow-y-auto"
      style={{ maxHeight: '80vh' }}
      role="list"
      aria-label={`${positions.length} open positions, virtualized grid`}
    >
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
        {virtualizer.getVirtualItems().map((virtualRow) => (
          <div
            key={virtualRow.key}
            data-index={virtualRow.index}
            ref={virtualizer.measureElement}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${virtualRow.start}px)`,
            }}
            className="pb-3"
          >
            <div className={`grid ${gridColsClass} gap-3`}>
              {rows[virtualRow.index].map((pos, i) => (
                <PositionCard
                  key={pos.id}
                  position={pos}
                  index={i}
                  sparklineSlot={renderSparkline ? renderSparkline(pos) : null}
              onSelect={onSelectPosition}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
