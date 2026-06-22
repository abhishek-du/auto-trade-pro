// Datetime helpers for backend timestamps.
//
// The backend stores/serves datetimes as UTC-naive ISO strings (e.g.
// "2026-06-22T03:55:13.084943") — no 'Z' or offset. JavaScript's `new Date(s)`
// parses a tz-less datetime as LOCAL time, so a 03:55 UTC fill (= 09:25 IST)
// wrongly rendered as "3:55 AM" and made elapsed/holding time off by 5h30m.
//
// These helpers mark bare timestamps as UTC and render explicitly in IST (the
// market timezone), so trades/agent activity read correctly regardless of the
// viewer's browser timezone. Already-tz-aware strings are left untouched.

const IST = 'Asia/Kolkata';

export function asUTCDate(s) {
  if (!s) return null;
  if (s instanceof Date) return s;
  const str = String(s);
  // Has a timezone already (Z or ±HH:MM)? Use as-is; otherwise treat as UTC.
  return /([zZ]|[+-]\d{2}:?\d{2})$/.test(str) ? new Date(str) : new Date(str + 'Z');
}

export function fmtIST(s, opts = { dateStyle: 'medium', timeStyle: 'short' }) {
  const d = asUTCDate(s);
  if (!d || isNaN(d.getTime())) return '—';
  try {
    return d.toLocaleString('en-IN', { ...opts, timeZone: IST });
  } catch {
    return String(s);
  }
}
