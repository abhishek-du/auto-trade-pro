import { createContext, useCallback, useContext, useMemo, useState } from 'react';

// Single namespaced localStorage key for every /trades page preference
// (view mode, column config, filter presets, drawer/grouping state, etc.)
// -- mirrors the app's existing lazy-useState-plus-localStorage idiom
// (see SectorHeatmap.jsx's `heatmap_view` key, AuthContext.jsx's TOKEN_KEY)
// but consolidated into one JSON blob instead of many scattered keys, since
// the settings panel (Phase 7) needs to reset/export "all trades prefs" as
// a single unit.
const STORAGE_KEY = 'atp_trades_prefs_v1';

export const DEFAULT_PREFS = {
  positionsView: 'grid',            // 'grid' | 'table'
  positionsColumns: {
    // visible + order for the dense table view (Phase 3)
    order: [
      'symbol', 'source', 'direction', 'qty', 'entry', 'current',
      'value', 'pnl', 'pnlPct', 'slTpDistance', 'holdingTime',
    ],
    hidden: [],
  },
  filterPresets: [
    {
      id: 'losers-only',
      name: 'Losers only',
      builtin: true,
      filters: { direction: 'ALL', status: 'OPEN', source: 'ALL', pnlSign: 'NEGATIVE', search: '' },
    },
    {
      id: 'near-stop-loss',
      name: 'Near stop-loss',
      builtin: true,
      filters: { direction: 'ALL', status: 'OPEN', source: 'ALL', nearStopPct: 1.5, search: '' },
    },
  ],
  activePresetId: null,
  groupBySector: false,
  sidebarCollapsed: false,
};

function loadPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw);
    // Shallow-merge so new default keys introduced in later phases show up
    // for users with an older blob already saved, without losing their data.
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

function savePrefs(prefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage unavailable (private browsing / quota) -- prefs just
    // won't persist across reloads, not worth surfacing as an error.
  }
}

const TradesPreferencesContext = createContext(null);

export function TradesPreferencesProvider({ children }) {
  const [prefs, setPrefsState] = useState(loadPrefs);

  const setPrefs = useCallback((updater) => {
    setPrefsState((prev) => {
      const next = typeof updater === 'function' ? updater(prev) : { ...prev, ...updater };
      savePrefs(next);
      return next;
    });
  }, []);

  const resetPrefs = useCallback(() => {
    savePrefs(DEFAULT_PREFS);
    setPrefsState(DEFAULT_PREFS);
  }, []);

  const value = useMemo(() => ({ prefs, setPrefs, resetPrefs }), [prefs, setPrefs, resetPrefs]);

  return (
    <TradesPreferencesContext.Provider value={value}>
      {children}
    </TradesPreferencesContext.Provider>
  );
}

export function useTradesPreferences() {
  const ctx = useContext(TradesPreferencesContext);
  if (!ctx) {
    throw new Error('useTradesPreferences must be used within a TradesPreferencesProvider');
  }
  return ctx;
}
