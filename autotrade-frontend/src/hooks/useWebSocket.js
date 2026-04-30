import { useEffect, useRef, useState, useCallback } from 'react';

export function useWebSocket(path, { onMessage, reconnectDelay = 3000 } = {}) {
  const [status, setStatus] = useState('connecting');
  const wsRef = useRef(null);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    const url = `ws://localhost:8000${path}`;
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
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { status, send };
}
