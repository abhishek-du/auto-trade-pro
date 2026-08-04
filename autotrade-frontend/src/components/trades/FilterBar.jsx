import { useEffect, useState } from 'react';
import { Search } from 'lucide-react';

const DIRECTIONS = ['ALL', 'BUY', 'SELL'];

/**
 * Direction/source chip filters + debounced search for the open-positions
 * section. Status isn't exposed here as a manual chip -- every row in this
 * section is, by definition, an open position, so an OPEN/CLOSED/STOPPED
 * toggle would have nothing to actually filter. It's still a first-class
 * field in the saved-preset shape (utils/positionFilters.js) for
 * completeness/forward-compat with the trade-history table's own filters
 * in Phase 6, and the two seeded "Losers only" / "Near stop-loss" presets
 * use filter dimensions (pnlSign, nearStopPct) that aren't manually
 * constructible through this simple chip UI -- selecting a saved preset is
 * how a user reaches those, matching the brief's "configure ... plus a
 * search, name it, save it" flow without needing a full generic
 * filter-builder for a first pass.
 */
export default function FilterBar({ filters, onChange, sources, searchInputRef }) {
  const [searchDraft, setSearchDraft] = useState(filters.search ?? '');
  // Tracks the last `filters.search` value we've already synced into the
  // local draft, so an EXTERNAL change (e.g. applying a saved preset) can
  // update the box via the "adjust state during render" pattern -- same
  // fix as usePositionSparklineBuffer -- instead of an effect chasing the
  // prop after the fact.
  const [lastExternalSearch, setLastExternalSearch] = useState(filters.search ?? '');

  if ((filters.search ?? '') !== lastExternalSearch) {
    setLastExternalSearch(filters.search ?? '');
    setSearchDraft(filters.search ?? '');
  }

  // Debounce search -> onChange so typing doesn't re-filter/re-render the
  // (possibly 80+ item) list on every keystroke.
  useEffect(() => {
    const id = setTimeout(() => {
      if (searchDraft !== filters.search) onChange({ ...filters, search: searchDraft });
    }, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft]);

  return (
    <div className="flex items-center gap-3 flex-wrap text-xs">
      <div className="relative flex-1 min-w-[160px] max-w-xs">
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          ref={searchInputRef}
          type="text"
          value={searchDraft}
          onChange={(e) => setSearchDraft(e.target.value)}
          placeholder="Search symbol..."
          aria-label="Search positions by symbol. Press slash to focus from anywhere on the page."
          className="w-full bg-surface border border-border rounded-lg pl-8 pr-8 py-1.5 text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent"
        />
        {!searchDraft && (
          <kbd
            aria-hidden="true"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-mono bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-muted pointer-events-none"
          >
            /
          </kbd>
        )}
      </div>

      <div role="group" aria-label="Filter by direction" className="flex items-center rounded-lg border border-border overflow-hidden">
        {DIRECTIONS.map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => onChange({ ...filters, direction: d })}
            aria-pressed={filters.direction === d}
            className={`px-2.5 py-1.5 font-medium border-r border-border last:border-r-0 transition-colors ${
              filters.direction === d ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
            }`}
          >
            {d}
          </button>
        ))}
      </div>

      {sources.length > 1 && (
        <div role="group" aria-label="Filter by source" className="flex items-center rounded-lg border border-border overflow-hidden">
          {['ALL', ...sources].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onChange({ ...filters, source: s })}
              aria-pressed={filters.source === s}
              className={`px-2.5 py-1.5 font-medium border-r border-border last:border-r-0 transition-colors ${
                filters.source === s ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
