/**
 * MobileNav — bottom tab bar for screens < 768px (replaces the sidebar).
 *
 * Five tabs mapping onto the 6-section IA (Tools folds into "More"):
 *   Home · Discover · Search (⌘K) · Portfolio · More
 *
 * The center Search button opens the same GlobalSearch palette used on desktop.
 */
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import {
  LayoutDashboard, Compass, Search, Briefcase, Menu, X,
} from 'lucide-react';
import { SECTIONS } from './Sidebar';

const TABS = [
  { to: '/',          label: 'Home',      Icon: LayoutDashboard, end: true },
  { to: '/india',     label: 'Discover',  Icon: Compass },
  { type: 'search',   label: 'Search',    Icon: Search },
  { to: '/zerodha',   label: 'Portfolio', Icon: Briefcase },
  { type: 'more',     label: 'More',      Icon: Menu },
];

function MoreSheet({ open, onClose }) {
  const navigate = useNavigate();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    if (open) setMounted(true);
    else setTimeout(() => setMounted(false), 300); // Wait for exit animation
  }, [open]);

  if (!mounted && !open) return null;

  return (
    <div className="md:hidden fixed inset-0 z-[9997]" onClick={onClose}>
      <div 
        className={`absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0'}`} 
      />
      <div
        className={`absolute bottom-0 left-0 right-0 rounded-t-3xl border-t border-white/10 max-h-[85vh] overflow-y-auto shadow-2xl transition-transform duration-300 transform ${open ? 'translate-y-0' : 'translate-y-full'}`}
        style={{ background: '#0B1121' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-5 border-b border-white/5 sticky top-0 glass-panel/95 backdrop-blur z-10">
          <span className="text-slate-100 font-bold text-lg tracking-wide">Menu</span>
          <button onClick={onClose} className="bg-white/5 hover:bg-white/10 rounded-full p-2 transition-colors text-muted hover:text-slate-200">
            <X size={20} />
          </button>
        </div>
        <div className="p-6 space-y-8 pb-32">
          {SECTIONS.map((section) => (
            <div key={section.key}>
              <div className="flex items-center gap-2 text-cyan font-semibold text-xs tracking-widest uppercase mb-4">
                <section.Icon size={14} />
                {section.label}
              </div>
              <div className="grid grid-cols-2 gap-3">
                {section.items.map(it => {
                  return (
                    <button
                      key={it.to}
                      onClick={() => { navigate(it.to); onClose(); }}
                      className="flex flex-col items-start px-4 py-3 rounded-xl bg-white/[0.03] border border-white/[0.04] text-slate-300 text-sm hover:bg-white/[0.08] hover:border-white/10 hover:shadow-lg transition-all text-left group"
                    >
                      <it.Icon size={18} className="mb-2 text-muted group-hover:text-cyan transition-colors" />
                      <span className="font-medium">{it.label}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function MobileNav({ onSearchOpen }) {
  const { pathname } = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <>
      <MoreSheet open={moreOpen} onClose={() => setMoreOpen(false)} />
      <div className="md:hidden fixed bottom-4 left-4 right-4 z-[9996] pb-[env(safe-area-inset-bottom)] pointer-events-none">
        <nav
          className="flex items-stretch justify-around glass-panel/80 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl pointer-events-auto"
        >
          {TABS.map(tab => {
            if (tab.type === 'search') {
              return (
                <button
                  key="search"
                  onClick={onSearchOpen}
                  className="flex-1 flex flex-col items-center justify-center gap-1 py-2.5 relative group"
                >
                  <span className="w-12 h-12 -mt-6 rounded-full grid place-items-center shadow-lg transform group-hover:scale-105 transition-transform glass-panel">
                    <Search size={22} className="text-white" />
                  </span>
                  <span className="text-[10px] font-medium text-slate-400">Search</span>
                </button>
              );
            }
            if (tab.type === 'more') {
              const isActive = moreOpen;
              return (
                <button
                  key="more"
                  onClick={() => setMoreOpen(o => !o)}
                  className="flex-1 flex flex-col items-center justify-center gap-1 py-2.5 transition-colors"
                >
                  <tab.Icon size={22} className={isActive ? 'text-cyan' : 'text-slate-400'} />
                  <span className={`text-[10px] font-medium ${isActive ? 'text-cyan' : 'text-slate-400'}`}>{tab.label}</span>
                </button>
              );
            }
            const active = tab.end ? pathname === tab.to : pathname.startsWith(tab.to);
            return (
              <NavLink
                key={tab.to}
                to={tab.to}
                end={tab.end}
                className="flex-1 flex flex-col items-center justify-center gap-1 py-2.5 transition-colors"
              >
                <tab.Icon size={22} className={active ? 'text-cyan' : 'text-slate-400'} />
                <span className={`text-[10px] font-medium ${active ? 'text-cyan' : 'text-slate-400'}`}>{tab.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </div>
    </>
  );
}
