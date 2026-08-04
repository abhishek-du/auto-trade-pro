import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Bookmark, ChevronDown, Plus, Trash2 } from 'lucide-react';

let idCounter = 0;
function makePresetId() {
  idCounter += 1;
  return `preset-${Date.now()}-${idCounter}`;
}

/**
 * Saved filter-preset switcher for the open-positions FilterBar. Presets
 * (including the two seeded defaults, "Losers only" / "Near stop-loss")
 * live in TradesPreferencesContext (prefs.filterPresets), persisted to
 * localStorage under the same namespaced key as every other /trades
 * preference.
 */
export default function PresetsDropdown({ presets, activePresetId, currentFilters, onApply, onClear, onSave, onDelete }) {
  const prefersReducedMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const popoverRef = useRef(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e) {
      if (popoverRef.current && !popoverRef.current.contains(e.target) && !buttonRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onClickOutside);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onClickOutside);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  function handleSaveNew() {
    const name = window.prompt('Name this filter preset:');
    if (!name || !name.trim()) return;
    onSave({ id: makePresetId(), name: name.trim(), builtin: false, filters: currentFilters });
    setOpen(false);
  }

  const activePreset = presets.find((p) => p.id === activePresetId);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-[11px] font-medium text-muted hover:text-slate-200 hover:border-accent/50 transition-colors"
      >
        <Bookmark size={13} aria-hidden="true" />
        {activePreset ? activePreset.name : 'Presets'}
        <ChevronDown size={11} aria-hidden="true" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            ref={popoverRef}
            role="menu"
            aria-label="Saved filter presets"
            initial={prefersReducedMotion ? false : { opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={prefersReducedMotion ? undefined : { opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="absolute left-0 top-full mt-2 w-60 glass-panel rounded-xl p-2 z-30 shadow-2xl"
          >
            <ul className="space-y-0.5 max-h-64 overflow-y-auto">
              <li>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { onClear(); setOpen(false); }}
                  aria-current={!activePresetId}
                  className={`w-full text-left px-2 py-1.5 rounded-lg text-xs transition-colors ${
                    !activePresetId ? 'bg-accent/20 text-accent' : 'text-slate-200 hover:bg-white/[0.03]'
                  }`}
                >
                  All positions
                </button>
              </li>
              {presets.map((preset) => (
                <li key={preset.id} className="flex items-center gap-1">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => { onApply(preset); setOpen(false); }}
                    aria-current={preset.id === activePresetId}
                    className={`flex-1 text-left px-2 py-1.5 rounded-lg text-xs transition-colors ${
                      preset.id === activePresetId ? 'bg-accent/20 text-accent' : 'text-slate-200 hover:bg-white/[0.03]'
                    }`}
                  >
                    {preset.name}
                  </button>
                  {!preset.builtin && (
                    <button
                      type="button"
                      onClick={() => onDelete(preset.id)}
                      aria-label={`Delete preset ${preset.name}`}
                      className="p-1 rounded text-muted hover:text-loss transition-colors"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
            <div className="border-t border-border mt-1 pt-1">
              <button
                type="button"
                onClick={handleSaveNew}
                className="flex items-center gap-1.5 w-full text-left px-2 py-1.5 rounded-lg text-xs text-accent hover:bg-accent/10 transition-colors"
              >
                <Plus size={12} aria-hidden="true" />
                Save current filters as preset
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
