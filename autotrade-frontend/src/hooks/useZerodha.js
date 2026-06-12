// useZerodha — central Zerodha state hook used by the new Zerodha pages.
//
// Returns connection status, holdings, positions, orders, GTTs, P&L,
// margins, mutual fund holdings + SIPs, and helpers for the most common
// actions (preview margins, cancel order, delete GTT, sync holdings).
//
// Auto-polls /status every 30 s so the dot in the sidebar stays fresh.

import { useState, useEffect, useCallback, useRef } from 'react';

const API = '/api/v1/zerodha';

async function asJson(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j?.detail || j?.error || detail; } catch { /* ignore */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  try { return await res.json(); } catch { return null; }
}

const get  = (url)        => fetch(url).then(asJson);
const post = (url, body)  => fetch(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined,
}).then(asJson);
const put  = (url, body)  => fetch(url, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined,
}).then(asJson);
const del  = (url)        => fetch(url, { method: 'DELETE' }).then(asJson);

export function useZerodha(pollMs = 30000) {
  const [status,            setStatus]            = useState(null);
  const [holdings,          setHoldings]          = useState([]);
  const [positions,         setPositions]         = useState({ day: [], net: [] });
  const [orders,            setOrders]            = useState([]);
  const [gtts,              setGtts]              = useState([]);
  const [pnl,               setPnl]               = useState(null);
  const [margins,           setMargins]           = useState(null);
  const [mfHoldings,        setMfHoldings]        = useState([]);
  const [mfSips,            setMfSips]            = useState([]);
  const [loading,           setLoading]           = useState(true);
  const [liveTickerRunning, setLiveTickerRunning] = useState(false);
  const [error,             setError]             = useState(null);

  const mountedRef = useRef(true);

  // ── Load everything in parallel ────────────────────────────────────────────
  const loadAllData = useCallback(async () => {
    const calls = [
      get(`${API}/holdings`).catch(() => null),
      get(`${API}/positions`).catch(() => null),
      get(`${API}/orders`).catch(() => null),
      get(`${API}/gtt`).catch(() => null),
      get(`${API}/pnl`).catch(() => null),
      get(`${API}/margins`).catch(() => null),
      get(`${API}/mf/holdings`).catch(() => null),
      get(`${API}/mf/sips`).catch(() => null),
      get(`${API}/ticker/status`).catch(() => null),
    ];
    const [h, p, o, g, pl, m, mh, ms, ts] = await Promise.allSettled(calls);
    if (!mountedRef.current) return;
    if (h.status  === 'fulfilled' && h.value)  setHoldings(h.value.holdings ?? h.value ?? []);
    if (p.status  === 'fulfilled' && p.value)  setPositions({
      day: p.value.day ?? [],
      net: p.value.net ?? [],
    });
    if (o.status  === 'fulfilled' && o.value)  setOrders(o.value.orders ?? o.value ?? []);
    if (g.status  === 'fulfilled' && g.value)  setGtts(g.value.triggers ?? g.value ?? []);
    if (pl.status === 'fulfilled' && pl.value) setPnl(pl.value);
    if (m.status  === 'fulfilled' && m.value)  setMargins(m.value);
    if (mh.status === 'fulfilled' && mh.value) setMfHoldings(mh.value.holdings ?? mh.value ?? []);
    if (ms.status === 'fulfilled' && ms.value) setMfSips(ms.value.sips ?? ms.value ?? []);
    if (ts.status === 'fulfilled' && ts.value) setLiveTickerRunning(Boolean(ts.value.running));
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const s = await get(`${API}/status`);
      if (!mountedRef.current) return s;
      setStatus(s);
      setError(null);
      if (s?.connected) {
        await loadAllData();
      }
      return s;
    } catch (err) {
      if (mountedRef.current) setError(err);
      return null;
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [loadAllData]);

  // ── Mount / poll ────────────────────────────────────────────────────────
  useEffect(() => {
    mountedRef.current = true;
    loadStatus();
    const id = setInterval(loadStatus, pollMs);
    return () => { mountedRef.current = false; clearInterval(id); };
  }, [loadStatus, pollMs]);

  // ── Actions ─────────────────────────────────────────────────────────────
  const getLoginUrl = useCallback(async () => {
    const r = await get(`${API}/login-url`);
    if (r?.url) window.open(r.url, 'zerodha_login', 'width=600,height=720');
    return r;
  }, []);

  const logout = useCallback(async () => {
    const r = await post(`${API}/logout`);
    await loadStatus();
    return r;
  }, [loadStatus]);

  const previewMargins = useCallback((ordersList) =>
    post(`${API}/margins/basket`, { orders: ordersList, consider_positions: true }),
  []);

  const previewCharges = useCallback((ordersList) =>
    post(`${API}/charges/preview`, { orders: ordersList }),
  []);

  const placeOrder = useCallback((body) =>
    fetch(`${API}/orders`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Confirm-Real-Order': 'yes',
      },
      body: JSON.stringify(body),
    }).then(asJson),
  []);

  const cancelOrder = useCallback((orderId, variety = 'regular') =>
    del(`${API}/orders/${orderId}?variety=${encodeURIComponent(variety)}`),
  []);

  const placeGttSingle = useCallback((body) => post(`${API}/gtt/single`, body), []);
  const placeGttOco    = useCallback((body) => post(`${API}/gtt/oco`, body), []);
  const deleteGtt      = useCallback((triggerId) => del(`${API}/gtt/${triggerId}`), []);

  const startTicker    = useCallback(() => post(`${API}/ticker/start`), []);
  const stopTicker     = useCallback(() => post(`${API}/ticker/stop`), []);

  const syncHoldings   = useCallback(async () => {
    const r = await post(`${API}/sync`);
    await loadAllData();
    return r;
  }, [loadAllData]);

  const refresh = useCallback(() => loadStatus(), [loadStatus]);

  return {
    // State
    status, holdings, positions, orders, gtts, pnl, margins,
    mfHoldings, mfSips, loading, liveTickerRunning, error,
    // Actions
    getLoginUrl, logout, previewMargins, previewCharges, placeOrder,
    cancelOrder, placeGttSingle, placeGttOco, deleteGtt,
    startTicker, stopTicker, syncHoldings, refresh,
  };
}
