// D4 (audit 2026-08-19): WebSocket endpoints now require the admin JWT.
//
// Browsers cannot set an Authorization header on `new WebSocket()`, so the
// backend's require_ws_auth dependency reads the token from a query parameter
// and closes the socket with 1008 if it is missing or invalid.
//
// Keep this the single place that knows how the token is attached — the three
// call sites (useWebSocket, LivePricesContext, CandlestickChart) all route
// through it so the scheme can change in one edit.
export const WS_TOKEN_KEY = 'atp_admin_token';

/** Append the stored admin token to a WebSocket URL. */
export function withWsToken(url) {
  const token = localStorage.getItem(WS_TOKEN_KEY);
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}
