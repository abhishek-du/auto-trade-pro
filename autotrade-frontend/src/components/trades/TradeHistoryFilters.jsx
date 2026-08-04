import { Search, Download, GitCompare } from 'lucide-react';

const DIRECTIONS = ['All', 'BUY', 'SELL'];
const STATUSES = ['All', 'OPEN', 'CLOSED', 'STOPPED'];
const SOURCES = ['All', 'Direct News', 'AI Predict'];

/**
 * Filter/search/export controls for the trade-history table. Search is
 * left uncontrolled-debounce-free here (unlike FilterBar's positions
 * search) since trade history filtering happens client-side over an
 * already-fetched array, not against 80+ live-updating cards -- a
 * keystroke-per-render re-filter of the trades list is cheap.
 */
export default function TradeHistoryFilters({
  search, onSearchChange,
  direction, onDirectionChange,
  status, onStatusChange,
  source, onSourceChange,
  compareOpen, onCompareOpenChange,
  resultCount,
  onExport,
}) {
  return (
    <div className="p-4 border-b border-border flex flex-wrap items-center gap-3">
      <div className="relative flex-1 min-w-40">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          type="text"
          placeholder="Search symbol…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          aria-label="Search trade history by symbol"
          className="w-full bg-surface border border-border rounded-lg pl-8 pr-3 py-2 text-sm text-slate-200 placeholder:text-muted focus:outline-none focus:border-accent"
        />
      </div>

      {[
        { label: 'Direction', value: direction, set: onDirectionChange, opts: DIRECTIONS },
        { label: 'Status', value: status, set: onStatusChange, opts: STATUSES },
        { label: 'Source', value: source, set: onSourceChange, opts: SOURCES },
      ].map(({ label, value, set, opts }) => (
        <div key={label} className="flex items-center gap-2">
          <span className="text-muted text-xs">{label}:</span>
          <div role="group" aria-label={`Filter by ${label.toLowerCase()}`} className="flex rounded-lg overflow-hidden border border-border">
            {opts.map((o) => (
              <button
                key={o}
                type="button"
                onClick={() => set(o)}
                aria-pressed={value === o}
                className={[
                  'px-3 py-2 text-xs font-medium transition-colors',
                  value === o ? 'bg-accent text-white' : 'text-muted hover:text-slate-300 hover:bg-surface',
                ].join(' ')}
              >
                {o}
              </button>
            ))}
          </div>
        </div>
      ))}

      <button
        type="button"
        onClick={() => onCompareOpenChange(!compareOpen)}
        aria-pressed={compareOpen}
        title="Show only trades whose symbol also has an open position right now"
        className={`flex items-center gap-1.5 px-2.5 py-2 rounded-lg border font-medium text-xs transition-colors ${
          compareOpen ? 'bg-accent/20 text-accent border-accent/40' : 'border-border text-muted hover:text-slate-200'
        }`}
      >
        <GitCompare size={13} aria-hidden="true" /> Compare to open positions
      </button>

      <button
        type="button"
        onClick={onExport}
        title="Export the currently filtered trades as CSV"
        className="flex items-center gap-1.5 px-2.5 py-2 rounded-lg border border-border text-muted hover:text-slate-200 hover:border-accent/50 font-medium text-xs transition-colors"
      >
        <Download size={13} aria-hidden="true" /> Export CSV
      </button>

      <span className="text-muted text-xs ml-auto font-medium">{resultCount} trades</span>
    </div>
  );
}
