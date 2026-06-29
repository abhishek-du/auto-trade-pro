import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import { useWebSocket } from './useWebSocket';

function normalizeAgentTrade(t) {
  const notional = (t.qty ?? 0) * (t.entry_price ?? 0);
  const pnlPct   = notional > 0 && t.pnl != null
    ? (t.pnl / notional) * 100
    : 0;
  return {
    id:                   `agent_${t.id}`,
    symbol:               t.symbol?.replace('.NS', '') ?? t.symbol,
    direction:            t.side,
    status:               t.exit_price != null ? 'CLOSED' : 'OPEN',
    entry_price:          t.entry_price,
    exit_price:           t.exit_price ?? null,
    stop_loss:            t.stop_price,
    take_profit:          t.target_price,
    size_units:           t.qty,
    size_usd:             notional,
    pnl:                  t.pnl ?? null,
    pnl_percent:          pnlPct,
    signal_confidence:    null,
    pattern_name:         t.strategy,
    ai_reason:            null,
    opened_at:            t.entry_ts,
    closed_at:            t.exit_ts ?? null,
    exit_reason:          t.exit_reason ?? null,
    current_price:        t.current_price ?? null,
    unrealised_pnl:       t.unrealised_pnl ?? null,
    unrealised_pct:       t.unrealised_pct ?? null,
    source:               'agent',
  };
}

export function useTrades(pollInterval = 30000) {
  const [trades, setTrades]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      // paper_trades is the single source of truth — the agent executor now
      // writes there too, so fetching /agent/trades as well would double-list
      // every agent trade. One source = no duplicates.
      const paperRaw = await apiFetch('/api/v1/portfolio/trades?limit=500').catch(() => []);
      const paper = (Array.isArray(paperRaw) ? paperRaw : []);
      paper.sort((a, b) => new Date(b.opened_at ?? 0) - new Date(a.opened_at ?? 0));
      setTrades(paper);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, pollInterval);
    return () => clearInterval(id);
  }, [fetchAll, pollInterval]);

  // WebSocket: patch current_price + unrealised P&L for open positions in real-time
  const onPnlPatch = useCallback((msg) => {
    if (msg.type !== 'pnl_patch' || !Array.isArray(msg.positions)) return;
    const patchMap = {};
    for (const p of msg.positions) patchMap[p.id] = p;
    setTrades(prev => prev.map(t => {
      const patch = patchMap[t.id];
      if (!patch) return t;
      return {
        ...t,
        current_price:  patch.current_price,
        unrealised_pnl: patch.unrealised_pnl,
        unrealised_pct: patch.unrealised_pct,
      };
    }));
  }, []);

  useWebSocket('/ws/positions-pnl', { onMessage: onPnlPatch });

  return { trades, loading, error, refetch: fetchAll };
}
