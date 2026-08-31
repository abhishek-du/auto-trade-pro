// useBroker — the single source of truth for "which broker is serving us".
//
// Every page that wants to name the data source reads it from here rather than
// hardcoding a broker name. Before the Upstox migration, five different pages
// each asserted "Zerodha" in prose; when Kite's token expired for good, all
// five kept saying it. A hardcoded broker name is a claim that silently rots.
//
// Backed by GET /api/v1/broker/status (anonymous, read-only).
//
//   state: 'down'      no usable broker — there is no live price source
//          'degraded'  quotes are being polled; the tick feed is not connected
//          'connected' streaming

import { useState, useEffect } from 'react';
import { apiFetch } from '../api/client';

export function useBroker(pollMs = 60000) {
  const [broker, setBroker] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      apiFetch('/api/v1/broker/status')
        .then(d => { if (alive) setBroker(d); })
        .catch(() => { if (alive) setBroker(null); });
    load();
    const id = setInterval(load, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);

  return {
    broker,
    // Name to render in prose. Never invents one: with no usable broker the
    // honest answer is "no broker", not a stale label.
    name: broker?.active_name || (broker ? 'no broker' : null),
    state: broker?.state || null,
    // True only when ticks are actually arriving.
    streaming: broker?.state === 'connected',
  };
}
