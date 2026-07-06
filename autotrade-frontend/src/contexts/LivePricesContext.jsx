/**
 * LivePricesContext — single WebSocket connection for the whole app.
 *
 * Replaces the per-component useLiveMarket() hook pattern that was opening
 * one WebSocket per page. Now there is exactly ONE connection at the App root
 * and every consumer reads from the same snapshot via useLivePrices().
 */
import { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import { getLivePrices, getMarketSummary, getTopMovers } from '../api/client';

const RECONNECT_MS  = 5_000;
const REST_POLL_MS  = 30_000;

function wsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/live-prices`;
}

const LivePricesContext = createContext({
  prices:         {},
  summary:        null,
  topMovers:      null,
  connected:      false,
  lastUpdated:    null,
  lastAgentEvent: null,
  lastNewsItem:   null,
});

export function LivePricesProvider({ children }) {
  const [prices,         setPrices]         = useState({});
  const [summary,        setSummary]        = useState(null);
  const [topMovers,      setTopMovers]      = useState(null);
  const [connected,      setConnected]      = useState(false);
  const [lastUpdated,    setLastUpdated]    = useState(null);
  const [lastAgentEvent, setLastAgentEvent] = useState(null);
  const [lastNewsItem,   setLastNewsItem]   = useState(null);

  const wsRef        = useRef(null);
  const pingRef      = useRef(null);
  const reconnectRef = useRef(null);
  const restPollRef  = useRef(null);
  const mountedRef   = useRef(true);

  // ── REST fallback / initial load ──────────────────────────────────────────
  const loadRest = useCallback(() => {
    getLivePrices()
      .then(data => { if (mountedRef.current) { setPrices(p => ({ ...p, ...data })); setLastUpdated(new Date()); } })
      .catch(() => {});
    getMarketSummary()
      .then(data => { if (mountedRef.current) setSummary(data); })
      .catch(() => {});
  }, []);

  const loadMovers = useCallback(() => {
    getTopMovers()
      .then(data => { if (mountedRef.current) setTopMovers(data); })
      .catch(() => {});
  }, []);

  // ── WebSocket (primary) ───────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState < 2) return;

    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      setConnected(true);
      clearTimeout(reconnectRef.current);
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 30_000);
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'full_snapshot') {
          setPrices(msg.data);
          if (msg.market_summary) setSummary(msg.market_summary);
          setLastUpdated(new Date());
        } else if (msg.type === 'price_update') {
          setPrices(prev => ({ ...prev, ...msg.data }));
          if (msg.market_summary) setSummary(msg.market_summary);
          setLastUpdated(new Date());
        } else if (msg.type === 'agent_event') {
          setLastAgentEvent(msg);
        } else if (msg.type === 'news_item') {
          setLastNewsItem(msg);
        }
      } catch { /* ignore malformed frames */ }
    };

    ws.onclose = () => {
      clearInterval(pingRef.current);
      if (!mountedRef.current) return;
      setConnected(false);
      
      const retries = reconnectRef.current?.retries || 0;
      if (retries < 3) {
        reconnectRef.current = setTimeout(() => {
          reconnectRef.current.retries = retries + 1;
          connect();
        }, RECONNECT_MS);
      } else {
        console.warn('WebSocket failed 3 times. Falling back to REST polling entirely.');
      }
    };

    ws.onerror = () => {
      clearInterval(pingRef.current);
      setConnected(false);
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    loadRest();
    loadMovers();
    connect();

    // REST fallback poll (in case WS is down / stale)
    restPollRef.current = setInterval(loadRest, REST_POLL_MS);
    const moversId = setInterval(loadMovers, 30_000);

    return () => {
      mountedRef.current = false;
      clearInterval(restPollRef.current);
      clearInterval(moversId);
      clearInterval(pingRef.current);
      clearTimeout(reconnectRef.current);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws && ws.readyState !== WebSocket.CONNECTING) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [loadRest, loadMovers, connect]);

  return (
    <LivePricesContext.Provider value={{ prices, summary, topMovers, connected, lastUpdated, lastAgentEvent, lastNewsItem }}>
      {children}
    </LivePricesContext.Provider>
  );
}

/** Full prices map + connection metadata — same shape as the old useLiveMarket(). */
export function useLivePrices() {
  return useContext(LivePricesContext);
}

/** Single-symbol lookup. Returns { price, change, change_pct, ... } or null. */
export function useLivePrice(symbol) {
  const { prices } = useContext(LivePricesContext);
  if (!symbol) return null;
  return prices[symbol] ?? prices[symbol?.replace('.NS', '')] ?? null;
}
