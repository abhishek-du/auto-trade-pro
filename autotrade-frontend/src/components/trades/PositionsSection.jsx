import { useMemo, useRef, useState } from 'react';
import { LayoutGrid, Table2, Layers } from 'lucide-react';
import PositionsGrid from './PositionsGrid';
import PositionsTable from './PositionsTable';
import PositionSparkline from './PositionSparkline';
import PositionDetailDrawer from './PositionDetailDrawer';
import FilterBar from './FilterBar';
import PresetsDropdown from './PresetsDropdown';
import SectorGroupHeader from './SectorGroupHeader';
import ShortcutsHelpModal from './ShortcutsHelpModal';
import { fmt } from '../../utils/tradeFormat';
import { DEFAULT_FILTERS, matchesFilters } from '../../utils/positionFilters';
import { useTradesPreferences } from '../../contexts/TradesPreferencesContext';
import { useKeyboardShortcuts } from '../../hooks/trades/useKeyboardShortcuts';

/**
 * Orchestrates the open-positions section: live-price enrichment (same math
 * as the original OpenPositionsSection in pages/Trades.jsx), a strategy-
 * source join against `trades` (OpenPositionOut has no `source` field --
 * confirmed via api/schemas.py -- so this is a real join by trade_id, not a
 * fabricated value), filtering/presets, sector grouping (real backend
 * `sector` field, added 2026-08-04 -- not a frontend mock), and the
 * grid/table view toggle.
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
  const [filters, setFilters] = useState(() => {
    const active = prefs.filterPresets.find((p) => p.id === prefs.activePresetId);
    return active ? { ...DEFAULT_FILTERS, ...active.filters } : DEFAULT_FILTERS;
  });
  const [collapsedSectors, setCollapsedSectors] = useState(() => new Set());
  const [helpOpen, setHelpOpen] = useState(false);
  const searchInputRef = useRef(null);

  useKeyboardShortcuts({
    searchInputRef,
    onToggleView: () => setPrefs((p) => ({ ...p, positionsView: p.positionsView === 'grid' ? 'table' : 'grid' })),
    onShowHelp: () => setHelpOpen(true),
  });

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
      const sector = pos.sector || 'GENERAL';
      return { ...pos, current_price, unrealised_pnl, unrealised_pct, strategySource, sector };
    });
  }, [positions, livePrices, tradeById]);

  const sources = useMemo(
    () => [...new Set(enriched.map((p) => p.strategySource).filter(Boolean))].sort(),
    [enriched],
  );

  const filtered = useMemo(
    () => enriched.filter((p) => matchesFilters(p, filters)),
    [enriched, filters],
  );

  const totalInvested = useMemo(() => filtered.reduce((s, p) => s + (p.size_usd ?? 0), 0), [filtered]);
  const totalUnrealised = useMemo(() => filtered.reduce((s, p) => s + (p.unrealised_pnl ?? 0), 0), [filtered]);
  const isGain = totalUnrealised >= 0;
  const selectedPosition = useMemo(
    () => enriched.find((p) => p.id === selectedId) ?? null,
    [enriched, selectedId],
  );

  const groups = useMemo(() => {
    if (!prefs.groupBySector) return null;
    const map = new Map();
    for (const p of filtered) {
      if (!map.has(p.sector)) map.set(p.sector, []);
      map.get(p.sector).push(p);
    }
    return [...map.entries()]
      .map(([sector, items]) => ({
        sector,
        items,
        totalPnl: items.reduce((s, p) => s + (p.unrealised_pnl ?? 0), 0),
      }))
      .sort((a, b) => b.items.length - a.items.length);
  }, [filtered, prefs.groupBySector]);

  function toggleSector(sector) {
    setCollapsedSectors((prev) => {
      const next = new Set(prev);
      if (next.has(sector)) next.delete(sector); else next.add(sector);
      return next;
    });
  }

  function handleApplyPreset(preset) {
    setFilters({ ...DEFAULT_FILTERS, ...preset.filters });
    setPrefs({ activePresetId: preset.id });
  }

  function handleClearPreset() {
    setFilters(DEFAULT_FILTERS);
    setPrefs({ activePresetId: null });
  }

  function handleSavePreset(newPreset) {
    setPrefs((p) => ({
      ...p,
      filterPresets: [...p.filterPresets, newPreset],
      activePresetId: newPreset.id,
    }));
  }

  function handleDeletePreset(id) {
    setPrefs((p) => ({
      ...p,
      filterPresets: p.filterPresets.filter((preset) => preset.id !== id),
      activePresetId: p.activePresetId === id ? null : p.activePresetId,
    }));
  }

  if (!positions || positions.length === 0) return null;

  const renderList = (items) => (
    view === 'table' ? (
      <PositionsTable positions={items} onSelectPosition={(p) => setSelectedId(p.id)} />
    ) : (
      <PositionsGrid
        positions={items}
        onSelectPosition={(p) => setSelectedId(p.id)}
        renderSparkline={(pos) => <PositionSparkline currentPrice={pos.current_price} />}
      />
    )
  );

  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-profit animate-pulse" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-200">
            Open Positions
            <span className="ml-2 text-xs font-normal text-muted">
              {filtered.length} of {positions.length} · live P&amp;L
            </span>
          </h2>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-muted">Notional exposure: <span className="text-slate-300 font-medium">{fmt(totalInvested)}</span></span>
          <span className={`font-semibold ${isGain ? 'text-profit' : 'text-loss'}`}>
            {isGain ? '+' : ''}{fmt(totalUnrealised)} unrealised
          </span>

          <button
            type="button"
            onClick={() => setPrefs({ groupBySector: !prefs.groupBySector })}
            aria-pressed={prefs.groupBySector}
            className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-border font-medium transition-colors ${
              prefs.groupBySector ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
            }`}
            title="Group by sector"
          >
            <Layers size={13} aria-hidden="true" /> By sector
          </button>

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

          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            aria-label="Show keyboard shortcuts"
            title="Keyboard shortcuts"
            className="flex items-center justify-center w-6 h-6 rounded-lg border border-border text-muted hover:text-slate-200 hover:border-accent/50 transition-colors text-[11px] font-mono"
          >
            ?
          </button>
        </div>
      </div>

      {/* Filters + presets */}
      <div className="flex items-center gap-2 flex-wrap">
        <PresetsDropdown
          presets={prefs.filterPresets}
          activePresetId={prefs.activePresetId}
          currentFilters={filters}
          onApply={handleApplyPreset}
          onClear={handleClearPreset}
          onSave={handleSavePreset}
          onDelete={handleDeletePreset}
        />
        <FilterBar filters={filters} onChange={setFilters} sources={sources} searchInputRef={searchInputRef} />
      </div>

      {groups ? (
        <div className="space-y-3">
          {groups.map(({ sector, items, totalPnl }) => (
            <div key={sector} className="space-y-2">
              <SectorGroupHeader
                sector={sector}
                count={items.length}
                totalPnl={totalPnl}
                collapsed={collapsedSectors.has(sector)}
                onToggle={() => toggleSector(sector)}
              />
              {!collapsedSectors.has(sector) && renderList(items)}
            </div>
          ))}
        </div>
      ) : (
        renderList(filtered)
      )}

      <PositionDetailDrawer position={selectedPosition} onClose={() => setSelectedId(null)} />
      <ShortcutsHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
