import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Settings2, GripVertical, ChevronUp, ChevronDown, Eye, EyeOff } from 'lucide-react';
import { POSITION_COLUMNS } from '../../utils/positionColumns';

/**
 * Show/hide/reorder popover for the dense table view, persisted via
 * TradesPreferencesContext (prefs.positionsColumns = { order, hidden }).
 *
 * Reorder is up/down-button-based rather than drag-and-drop: no drag
 * library is installed and this keeps the control fully keyboard-operable
 * (Tab + Enter) without adding a dependency for what's fundamentally a
 * short, linear list.
 */
export default function ColumnManagerPopover({ columns, onChange }) {
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

  const orderedColumns = columns.order
    .map((id) => POSITION_COLUMNS.find((c) => c.id === id))
    .filter(Boolean);

  function toggleHidden(id) {
    const hidden = columns.hidden.includes(id)
      ? columns.hidden.filter((h) => h !== id)
      : [...columns.hidden, id];
    onChange({ ...columns, hidden });
  }

  function move(id, direction) {
    const idx = columns.order.indexOf(id);
    const swapWith = idx + direction;
    if (swapWith < 0 || swapWith >= columns.order.length) return;
    const order = [...columns.order];
    [order[idx], order[swapWith]] = [order[swapWith], order[idx]];
    onChange({ ...columns, order });
  }

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
        <Settings2 size={13} aria-hidden="true" />
        Manage columns
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            ref={popoverRef}
            role="dialog"
            aria-label="Manage table columns"
            initial={prefersReducedMotion ? false : { opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={prefersReducedMotion ? undefined : { opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 top-full mt-2 w-64 glass-panel rounded-xl p-2 z-30 shadow-2xl"
          >
            <p className="text-[10px] text-muted uppercase tracking-wide px-2 py-1.5">
              Show, hide, and reorder columns
            </p>
            <ul className="space-y-0.5 max-h-80 overflow-y-auto">
              {orderedColumns.map((col, i) => {
                const isHidden = columns.hidden.includes(col.id);
                return (
                  <li
                    key={col.id}
                    className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg hover:bg-white/[0.03]"
                  >
                    <GripVertical size={13} className="text-muted/50 shrink-0" aria-hidden="true" />
                    <span className={`flex-1 text-xs ${isHidden ? 'text-muted/60' : 'text-slate-200'}`}>
                      {col.label}
                    </span>
                    <button
                      type="button"
                      onClick={() => move(col.id, -1)}
                      disabled={i === 0}
                      aria-label={`Move ${col.label} up`}
                      className="p-1 rounded text-muted hover:text-slate-200 disabled:opacity-20 disabled:pointer-events-none"
                    >
                      <ChevronUp size={12} />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(col.id, 1)}
                      disabled={i === orderedColumns.length - 1}
                      aria-label={`Move ${col.label} down`}
                      className="p-1 rounded text-muted hover:text-slate-200 disabled:opacity-20 disabled:pointer-events-none"
                    >
                      <ChevronDown size={12} />
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleHidden(col.id)}
                      aria-label={`${isHidden ? 'Show' : 'Hide'} ${col.label} column`}
                      aria-pressed={!isHidden}
                      className="p-1 rounded text-muted hover:text-slate-200"
                    >
                      {isHidden ? <EyeOff size={13} /> : <Eye size={13} />}
                    </button>
                  </li>
                );
              })}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
