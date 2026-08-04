import { formatINR } from './indianFormat';
import { asUTCDate } from './datetime';

// Extracted from pages/Trades.jsx (2026-08-04 redesign) so the new
// components/trades/* pieces and the not-yet-migrated trade-history table
// in Trades.jsx share exactly one implementation instead of drifting.

export const fmt = (n, dec = 2) => formatINR(n ?? 0, dec);

/* Show fractional shares with 1 decimal; never show "0 shares" for a real position */
export const fmtQty = (q) => {
  const n = q ?? 0;
  const frac = n % 1;
  return (frac > 0.05 && frac < 0.95) ? n.toFixed(1) : Math.round(n).toFixed(0);
};

export function elapsed(openedAt, closedAt = null) {
  if (!openedAt) return '—';
  const end  = closedAt ? asUTCDate(closedAt) : new Date();
  const ms   = end - asUTCDate(openedAt);
  const mins = Math.floor(ms / 60000);
  if (mins < 60)  return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs  < 24)  return `${hrs}h ${mins % 60}m`;
  return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
}
