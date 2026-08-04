export const DEFAULT_FILTERS = {
  direction: 'ALL',
  source: 'ALL',
  search: '',
  pnlSign: 'ALL',       // 'ALL' | 'POSITIVE' | 'NEGATIVE' -- preset-only (see FilterBar.jsx)
  nearStopPct: undefined, // number | undefined -- preset-only, "within N% of stop-loss"
};

/**
 * Returns true if `pos` (an enriched position from PositionsSection) matches
 * `filters`. Shared between the manual FilterBar controls and the two
 * seeded presets ("Losers only" -> pnlSign: 'NEGATIVE', "Near stop-loss" ->
 * nearStopPct: 1.5) so both paths run through one implementation.
 */
export function matchesFilters(pos, filters) {
  const f = { ...DEFAULT_FILTERS, ...filters };

  if (f.direction !== 'ALL' && pos.direction?.toUpperCase() !== f.direction) return false;
  if (f.source !== 'ALL' && pos.strategySource !== f.source) return false;

  if (f.search) {
    const needle = f.search.trim().toUpperCase();
    if (needle && !pos.symbol?.toUpperCase().includes(needle)) return false;
  }

  if (f.pnlSign === 'POSITIVE' && (pos.unrealised_pnl ?? 0) < 0) return false;
  if (f.pnlSign === 'NEGATIVE' && (pos.unrealised_pnl ?? 0) >= 0) return false;

  if (f.nearStopPct != null && pos.stop_loss && pos.current_price) {
    const dist = Math.abs((pos.current_price - pos.stop_loss) / pos.current_price * 100);
    if (dist > f.nearStopPct) return false;
  }

  return true;
}
