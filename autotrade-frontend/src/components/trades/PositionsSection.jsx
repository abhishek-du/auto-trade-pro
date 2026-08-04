import { useMemo, useState } from 'react';
import { LayoutGrid, Table2 } from 'lucide-react';
import PositionsGrid from './PositionsGrid';
import PositionsTable from './PositionsTable';
import PositionSparkline from './PositionSparkline';
import PositionDetailDrawer from './PositionDetailDrawer';
import { fmt } from '../../utils/tradeFormat';
import { useTradesPreferences } from '../../contexts/TradesPreferencesContext';

/**
 * Orchestrates the open-positions section: live-price enrichment (same math
 * as the original OpenPositionsSection in pages/Trades.jsx), a strategy-
 * source join against `trades` (OpenPositionOut has no `source` field --
 * confirmed via api/schemas.py -- so this is a real join by trade_id, not a
 * fabricated value), and the grid/table view toggle.
 *
 * `trades` join key: /api/v1/portfolio/trades rows carry `id` as the string
 * "paper_<PaperTrade.id>" (confirmed in api/portfolio.py), while
 * OpenPositionOut.trade_id is the plain PaperTrade.id int -- reconstructing
 * the string form to match rather than stripping prefixes both ways.
 */
export default function PositionsSection({ positions, livePrices = {}, trades = [] }) {
  const { prefs, setPrefs } = useTradesPreferences();
  const view = prefs.positionsView; // 'grid' | 'table'
  const [selectedId, setSelectedId] = useState(null);

  const tradeById = useMemo(() => {
    const m = new Map();
    for (const t of trades) m.set(t.id, t);
    return m;
  }, [trades]);

  const enriched = useMemo(() => {
    return (positions || []).map((pos) => {
      const bare = (pos.symbol ?? '').replace('.NS', '').toUpperCase();
      const liveD = livePrices[bare + '.NS'] || livePrices[bare] || null;
      const current_price = liveD?.price ?? pos.current_price;
      const qty = pos.size_units ?? (pos.size_usd / (pos.entry_price || 1));
      const isBuy = pos.direction?.toUpperCase() === 'BUY';
      const unrealised_pnl = liveD
        ? (current_price - pos.entry_price) * qty * (isBuy ? 1 : -1)
        : (pos.unrealised_pnl ?? 0);
      const unrealised_pct = pos.size_usd
        ? unrealised_pnl / pos.size_usd * 100
        : (pos.unrealised_pct ?? 0);
      const linkedTrade = tradeById.get(`paper_${pos.trade_id}`);
      const strategySource = linkedTrade?.strategy_source ?? 'Unknown';
      return { ...pos, current_price, unrealised_pnl, unrealised_pct, strategySource };
    });
  }, [positions, livePrices, tradeById]);

  const totalInvested = useMemo(() => enriched.reduce((s, p) => s + (p.size_usd ?? 0), 0), [enriched]);
  const totalUnrealised = useMemo(() => enriched.reduce((s, p) => s + (p.unrealised_pnl ?? 0), 0), [enriched]);
  const isGain = totalUnrealised >= 0;
  const selectedPosition = useMemo(
    () => enriched.find((p) => p.id === selectedId) ?? null,
    [enriched, selectedId],
  );

  if (!positions || positions.length === 0) return null;

  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-profit animate-pulse" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-200">
            Open Positions
            <span className="ml-2 text-xs font-normal text-muted">
              {positions.length} active · live P&amp;L
            </span>
          </h2>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-muted">Notional exposure: <span className="text-slate-300 font-medium">{fmt(totalInvested)}</span></span>
          <span className={`font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>
            {isGain ? '+' : ''}{fmt(totalUnrealised)} unrealised
          </span>

          <div
            role="group"
            aria-label="Positions view"
            className="flex items-center rounded-lg border border-border overflow-hidden"
          >
            <button
              type="button"
              onClick={() => setPrefs({ positionsView: 'grid' })}
              aria-pressed={view === 'grid'}
              className={`flex items-center gap-1 px-2 py-1 text-[11px] font-medium transition-colors ${
                view === 'grid' ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
              }`}
              title="Grid view"
            >
              <LayoutGrid size={13} aria-hidden="true" /> Grid
            </button>
            <button
              type="button"
              onClick={() => setPrefs({ positionsView: 'table' })}
              aria-pressed={view === 'table'}
              className={`flex items-center gap-1 px-2 py-1 text-[11px] font-medium border-l border-border transition-colors ${
                view === 'table' ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
              }`}
              title="Table view"
            >
              <Table2 size={13} aria-hidden="true" /> Table
            </button>
          </div>
        </div>
      </div>

      {view === 'table' ? (
        <PositionsTable positions={enriched} onSelectPosition={(p) => setSelectedId(p.id)} />
      ) : (
        <PositionsGrid
          positions={enriched}
          onSelectPosition={(p) => setSelectedId(p.id)}
          renderSparkline={(pos) => <PositionSparkline currentPrice={pos.current_price} />}
        />
      )}

      <PositionDetailDrawer position={selectedPosition} onClose={() => setSelectedId(null)} />
    </div>
  );
}
