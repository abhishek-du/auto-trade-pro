import { useEffect, useRef, useState, useCallback } from 'react';

export function useWebSocket(path, { onMessage, reconnectDelay = 3000 } = {}) {
  const [status, setStatus] = useState('connecting');
  const wsRef = useRef(null);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}${path}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen  = () => mountedRef.current && setStatus('connected');
    ws.onclose = () => {
      if (!mountedRef.current) return;
      setStatus('disconnected');
      timerRef.current = setTimeout(connect, reconnectDelay);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        onMessage?.(data);
      } catch {
        onMessage?.(evt.data);
      }
    };
  }, [path, onMessage, reconnectDelay]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);

      const ws = wsRef.current;
      if (!ws) return;

      // Detach handlers BEFORE closing: onclose schedules a reconnect, and a
      // teardown must not resurrect the socket it is tearing down.
      ws.onopen = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;

      if (ws.readyState === WebSocket.CONNECTING) {
        // Calling close() mid-handshake is what makes the browser log
        // "WebSocket is closed before the connection is established" — seen on
        // /ws/trades, /ws/portfolio and /ws/positions-pnl. React StrictMode
        // mounts, unmounts and remounts effects in dev, so the first socket is
        // always still CONNECTING when its cleanup runs. Let the handshake
        // finish, then close cleanly; without this the socket is also leaked,
        // since an aborted handshake never reaches onclose.
        ws.onopen = () => ws.close();
      } else if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [connect]);

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { status, send };
}
