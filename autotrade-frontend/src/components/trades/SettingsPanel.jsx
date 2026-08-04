import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, RotateCcw, LayoutGrid, Table2, Layers, Bookmark, Trash2 } from 'lucide-react';
import { useTradesPreferences } from '../../contexts/TradesPreferencesContext';

/**
 * Manages every persisted /trades preference (single localStorage blob,
 * see TradesPreferencesContext) from one place: view mode, sector
 * grouping, saved filter presets, and a reset-to-defaults escape hatch.
 * Column show/hide/order already has its own editor (ColumnManagerPopover)
 * -- this panel only shows how many columns are hidden, not a duplicate
 * editor, to avoid two competing UIs for the same state.
 */
export default function SettingsPanel({ open, onClose }) {
  const { prefs, setPrefs, resetPrefs } = useTradesPreferences();
  const prefersReducedMotion = useReducedMotion();
  const panelRef = useRef(null);
  const [confirmReset, setConfirmReset] = useState(false);

  // Reset the two-step confirm state whenever the panel transitions to open --
  // "adjust state during render" instead of an effect chasing `open`, same
  // fix as usePositionSparklineBuffer.js / FilterBar.jsx elsewhere on this page.
  const [lastOpen, setLastOpen] = useState(open);
  if (open !== lastOpen) {
    setLastOpen(open);
    if (open) setConfirmReset(false);
  }

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  const hiddenCols = prefs.positionsColumns?.hidden?.length ?? 0;
  const customPresets = prefs.filterPresets.filter((p) => !p.builtin);

  function handleDeletePreset(id) {
    setPrefs((p) => ({
      ...p,
      filterPresets: p.filterPresets.filter((preset) => preset.id !== id),
      activePresetId: p.activePresetId === id ? null : p.activePresetId,
    }));
  }

  function handleReset() {
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    resetPrefs();
    setConfirmReset(false);
    onClose();
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/60 z-40"
            aria-hidden="true"
          />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Trades page settings"
            tabIndex={-1}
            initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, x: 24 }}
            transition={{ duration: 0.2 }}
            className="fixed top-0 right-0 h-full w-full max-w-sm bg-[#0a1120] border-l border-border z-50 overflow-y-auto"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-sm font-semibold text-slate-200">Trades page settings</h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close settings"
                className="p-1.5 rounded-lg text-muted hover:text-slate-200 hover:bg-white/5 transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            <div className="p-5 space-y-6">
              <section>
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-muted mb-2.5">Open positions view</h3>
                <div role="group" aria-label="Positions view" className="flex items-center rounded-lg border border-border overflow-hidden w-fit">
                  <button
                    type="button"
                    onClick={() => setPrefs({ positionsView: 'grid' })}
                    aria-pressed={prefs.positionsView === 'grid'}
                    className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
                      prefs.positionsView === 'grid' ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
                    }`}
                  >
                    <LayoutGrid size={13} /> Grid
                  </button>
                  <button
                    type="button"
                    onClick={() => setPrefs({ positionsView: 'table' })}
                    aria-pressed={prefs.positionsView === 'table'}
                    className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-l border-border transition-colors ${
                      prefs.positionsView === 'table' ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-200'
                    }`}
                  >
                    <Table2 size={13} /> Table
                  </button>
                </div>
                {prefs.positionsView === 'table' && (
                  <p className="text-[11px] text-muted mt-2">
                    {hiddenCols > 0 ? `${hiddenCols} column${hiddenCols === 1 ? '' : 's'} hidden` : 'All columns visible'} — manage from the table's own "Manage columns" button.
                  </p>
                )}
              </section>

              <section>
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-muted mb-2.5">Sector grouping</h3>
                <button
                  type="button"
                  onClick={() => setPrefs({ groupBySector: !prefs.groupBySector })}
                  aria-pressed={prefs.groupBySector}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border font-medium text-xs transition-colors ${
                    prefs.groupBySector ? 'bg-accent/20 text-accent border-accent/40' : 'border-border text-muted hover:text-slate-200'
                  }`}
                >
                  <Layers size={13} /> Group open positions by sector
                </button>
              </section>

              <section>
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-muted mb-2.5">Saved filter presets</h3>
                <ul className="space-y-1.5">
                  {prefs.filterPresets.map((preset) => (
                    <li key={preset.id} className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-surface/60 border border-border">
                      <span className="flex items-center gap-1.5 text-xs text-slate-300">
                        <Bookmark size={12} className="text-muted" />
                        {preset.name}
                        {preset.builtin && <span className="text-[9px] text-muted uppercase tracking-wide">built-in</span>}
                      </span>
                      {!preset.builtin && (
                        <button
                          type="button"
                          onClick={() => handleDeletePreset(preset.id)}
                          aria-label={`Delete preset ${preset.name}`}
                          className="p-1 rounded text-muted hover:text-loss transition-colors"
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
                {customPresets.length === 0 && (
                  <p className="text-[11px] text-muted mt-2">No custom presets saved yet — save one from the Presets dropdown above the positions list.</p>
                )}
              </section>

              <section className="pt-4 border-t border-border/60">
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-muted mb-2.5">Reset</h3>
                <p className="text-[11px] text-muted mb-3">
                  Clears every saved preference on this page — view mode, columns, presets, and grouping — back to defaults.
                </p>
                <button
                  type="button"
                  onClick={handleReset}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-lg border font-medium text-xs transition-colors ${
                    confirmReset
                      ? 'bg-loss/20 text-loss border-loss/40'
                      : 'border-border text-muted hover:text-loss hover:border-loss/40'
                  }`}
                >
                  <RotateCcw size={13} /> {confirmReset ? 'Click again to confirm' : 'Reset to defaults'}
                </button>
              </section>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
