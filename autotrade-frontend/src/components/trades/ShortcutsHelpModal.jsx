import { useEffect, useRef } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X } from 'lucide-react';

const SHORTCUTS = [
  { keys: ['/'], desc: 'Focus symbol search' },
  { keys: ['⌘', 'K'], desc: 'Open command palette' },
  { keys: ['G', 'V'], desc: 'Toggle grid / table view' },
  { keys: ['J'], desc: 'Move to next position' },
  { keys: ['K'], desc: 'Move to previous position' },
  { keys: ['↓', '↑'], desc: 'Move to next / previous position' },
  { keys: ['Enter'], desc: 'Open selected position details' },
  { keys: ['Esc'], desc: 'Close drawer / popover' },
  { keys: ['?'], desc: 'Show this help' },
];

function Kbd({ children }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-md border border-border bg-surface text-[11px] font-mono text-slate-300 shadow-sm">
      {children}
    </kbd>
  );
}

export default function ShortcutsHelpModal({ open, onClose }) {
  const prefersReducedMotion = useReducedMotion();
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    panelRef.current?.focus();
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 bg-black/60 z-40"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
            tabIndex={-1}
            initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.95, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.95, y: -10 }}
            transition={{ duration: 0.18 }}
            className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm glass-panel rounded-xl p-5 z-50 focus:outline-none"
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-100">Keyboard shortcuts</h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="p-1 rounded-lg text-muted hover:text-slate-200 hover:bg-white/[0.05] transition-colors"
              >
                <X size={16} />
              </button>
            </div>
            <ul className="space-y-2.5">
              {SHORTCUTS.map(({ keys, desc }) => (
                <li key={desc} className="flex items-center justify-between text-xs">
                  <span className="text-muted">{desc}</span>
                  <span className="flex items-center gap-1">
                    {keys.map((k) => <Kbd key={k}>{k}</Kbd>)}
                  </span>
                </li>
              ))}
            </ul>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
