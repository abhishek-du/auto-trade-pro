import { useEffect, useRef } from 'react';

const NAV_SELECTOR = '[data-position-nav="true"]';

function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
}

/**
 * Page-scoped keyboard shortcuts for /trades:
 *   /        focus the position search box
 *   g then v toggle grid/table view
 *   j / k    move focus to the next/previous position card or row
 *   ArrowDown/ArrowUp  same as j/k
 *   ?        open the shortcuts help modal
 *   Esc      handled per-overlay (drawer, popovers) already, not here
 *
 * Cmd/Ctrl+K is intentionally NOT bound here -- it's already wired
 * app-wide to GlobalSearch (Navbar.jsx); duplicating it here would create
 * two competing handlers for the same chord.
 */
export function useKeyboardShortcuts({ searchInputRef, onToggleView, onShowHelp }) {
  const pendingGRef = useRef(false);
  const pendingGTimerRef = useRef(null);

  useEffect(() => {
    function onKeyDown(e) {
      const typing = isTypingTarget(document.activeElement);

      // 'g' then 'v' sequence -- works even while typing is NOT happening;
      // if the user is in a text field, only '?' and the nav keys are
      // suppressed, but 'g v' as a deliberate two-key chord outside of
      // typing contexts is safe to keep active everywhere except a text field.
      if (typing) {
        return;
      }

      if (e.key === '/') {
        e.preventDefault();
        searchInputRef?.current?.focus();
        return;
      }

      if (e.key === '?') {
        e.preventDefault();
        onShowHelp?.();
        return;
      }

      if (e.key.toLowerCase() === 'g' && !e.metaKey && !e.ctrlKey) {
        pendingGRef.current = true;
        clearTimeout(pendingGTimerRef.current);
        pendingGTimerRef.current = setTimeout(() => { pendingGRef.current = false; }, 800);
        return;
      }

      if (e.key.toLowerCase() === 'v' && pendingGRef.current) {
        pendingGRef.current = false;
        clearTimeout(pendingGTimerRef.current);
        onToggleView?.();
        return;
      }

      if (e.key === 'j' || e.key === 'ArrowDown') {
        const nodes = Array.from(document.querySelectorAll(NAV_SELECTOR));
        if (!nodes.length) return;
        e.preventDefault();
        const idx = nodes.indexOf(document.activeElement);
        const next = nodes[Math.min(idx + 1, nodes.length - 1)] ?? nodes[0];
        next.focus();
        return;
      }

      if (e.key === 'k' || e.key === 'ArrowUp') {
        const nodes = Array.from(document.querySelectorAll(NAV_SELECTOR));
        if (!nodes.length) return;
        e.preventDefault();
        const idx = nodes.indexOf(document.activeElement);
        const prev = nodes[Math.max(idx - 1, 0)] ?? nodes[0];
        prev.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      clearTimeout(pendingGTimerRef.current);
    };
  }, [searchInputRef, onToggleView, onShowHelp]);
}
