import { useState, useEffect, useCallback } from 'react';
import { getTrades, apiFetch } from '../api/client';

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

export function useTrades(pollInterval = 15000) {
  const [trades, setTrades]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      const [paperRaw, agentRaw] = await Promise.allSettled([
        getTrades(),
        apiFetch('/api/v1/agent/trades?limit=500'),
      ]);

      const paper = paperRaw.status === 'fulfilled'
        ? (Array.isArray(paperRaw.value) ? paperRaw.value : paperRaw.value?.trades ?? []).map(t => ({ ...t, source: 'scanner' }))
        : [];

      const agent = agentRaw.status === 'fulfilled'
        ? (Array.isArray(agentRaw.value) ? agentRaw.value : []).map(normalizeAgentTrade)
        : [];

      // Merge: agent trades first (most recent), then scanner trades
      // Sort by opened_at descending
      const merged = [...agent, ...paper].sort((a, b) => {
        const ta = new Date(a.opened_at ?? 0).getTime();
        const tb = new Date(b.opened_at ?? 0).getTime();
        return tb - ta;
      });

      setTrades(merged);
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

  return { trades, loading, error, refetch: fetchAll };
}
