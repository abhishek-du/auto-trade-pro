import { useCallback, useEffect, useRef } from 'react';
import toast from 'react-hot-toast';
import { useWebSocket } from '../useWebSocket';

const LARGE_SWING_PCT = 8;

/**
 * Subscribes to /ws/trades (TRADE_OPENED/TRADE_CLOSED, backed by SimulationLog
 * polling every 2s -- confirmed via api/websocket.py -- distinct from the
 * agent-event stream LivePricesContext already exposes) and surfaces
 * TP/SL hits, unusually large P&L swings on close, and feed disconnect/
 * reconnect as toasts. `reason` values (STOP_LOSS / TAKE_PROFIT) come from
 * position_tracker.py / trade_simulator.py's close paths.
 */
export function useTradeEventToasts() {
  const prevStatusRef = useRef('connecting');

  const onMessage = useCallback((msg) => {
    if (msg?.type !== 'trade_event') return;
    const sym = (msg.symbol ?? '').replace('.NS', '');
    const data = msg.data ?? {};

    if (msg.event === 'TRADE_OPENED') {
      toast(`Opened ${sym}`, { icon: '📈' });
      return;
    }

    if (msg.event === 'TRADE_CLOSED' || msg.event === 'TRADE_STOPPED') {
      const pnl = data.pnl ?? 0;
      const pnlPct = data.pnl_pct ?? 0;
      const sign = pnl >= 0 ? '+' : '';

      if (data.reason === 'STOP_LOSS') {
        toast.error(`${sym} stopped out — ${sign}₹${pnl.toFixed(0)} (${sign}${pnlPct.toFixed(1)}%)`);
      } else if (data.reason === 'TAKE_PROFIT') {
        toast.success(`${sym} hit target — ${sign}₹${pnl.toFixed(0)} (${sign}${pnlPct.toFixed(1)}%)`);
      } else if (Math.abs(pnlPct) >= LARGE_SWING_PCT) {
        toast(`${sym} closed with a large swing: ${sign}${pnlPct.toFixed(1)}%`, { icon: pnl >= 0 ? '🚀' : '⚠️' });
      } else {
        toast(`${sym} closed — ${sign}₹${pnl.toFixed(0)} (${sign}${pnlPct.toFixed(1)}%)`);
      }
    }
  }, []);

  const { status } = useWebSocket('/ws/trades', { onMessage });

  useEffect(() => {
    const prev = prevStatusRef.current;
    if (prev === 'connected' && status === 'disconnected') {
      toast.error('Trade feed disconnected — reconnecting…', { id: 'trade-feed-status' });
    } else if (prev === 'disconnected' && status === 'connected') {
      toast.success('Trade feed reconnected', { id: 'trade-feed-status' });
    }
    prevStatusRef.current = status;
  }, [status]);
}
