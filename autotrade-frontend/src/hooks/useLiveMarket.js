import { useState, useEffect, useRef, useCallback } from 'react';
import { getLivePrices, getMarketSummary, getTopMovers } from '../api/client';

const WS_URL = 'ws://localhost:8000/ws/live-prices';
const RECONNECT_MS = 5_000;

export function useLiveMarket() {
  const [prices,      setPrices]      = useState({});
  const [summary,     setSummary]     = useState(null);
  const [topMovers,   setTopMovers]   = useState(null);
  const [connected,   setConnected]   = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const wsRef        = useRef(null);
  const pingRef      = useRef(null);
  const reconnectRef = useRef(null);
  const mountedRef   = useRef(true);

  // ── REST initial load ──────────────────────────────────────────────────────
  const loadRest = useCallback(() => {
    getLivePrices()
      .then(data  => { if (mountedRef.current) { setPrices(data); setLastUpdated(new Date()); } })
      .catch(() => {});
    getMarketSummary()
      .then(data  => { if (mountedRef.current) setSummary(data); })
      .catch(() => {});
    getTopMovers()
      .then(data  => { if (mountedRef.current) setTopMovers(data); })
      .catch(() => {});
  }, []);

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState < 2) return; // already open/connecting

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      setConnected(true);
      clearTimeout(reconnectRef.current);
      // Ping every 30 s to keep the connection alive
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 30_000);
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'full_snapshot' || msg.type === 'price_update') {
          setPrices(prev => ({ ...prev, ...msg.data }));
          if (msg.market_summary) setSummary(msg.market_summary);
          setLastUpdated(new Date());
        }
      } catch { /* ignore malformed frames */ }
    };

    ws.onclose = () => {
      clearInterval(pingRef.current);
      if (!mountedRef.current) return;
      setConnected(false);
      reconnectRef.current = setTimeout(connect, RECONNECT_MS);
    };

    ws.onerror = () => {
      clearInterval(pingRef.current);
      setConnected(false);
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    loadRest();
    connect();

    // Poll top-movers every 30 s (not in WS stream)
    const moversId = setInterval(() => {
      getTopMovers()
        .then(data => { if (mountedRef.current) setTopMovers(data); })
        .catch(() => {});
    }, 30_000);

    return () => {
      mountedRef.current = false;
      clearInterval(moversId);
      clearInterval(pingRef.current);
      clearTimeout(reconnectRef.current);
      const ws = wsRef.current;
      wsRef.current = null;
      // Don't close a CONNECTING socket — its onopen handler will close it because
      // mountedRef.current is now false. Calling close() on readyState=0 triggers
      // a browser warning "WebSocket closed before connection established".
      if (ws && ws.readyState !== WebSocket.CONNECTING) {
        ws.onclose = null; // prevent the reconnect timer from firing
        ws.close();
      }
    };
  }, [loadRest, connect]);

  return { prices, summary, topMovers, connected, lastUpdated };
}
