/**
 * MobileNav — bottom tab bar for screens < 768px (replaces the sidebar).
 *
 * Five tabs mapping onto the 6-section IA (Tools folds into "More"):
 *   Home · Discover · Search (⌘K) · Portfolio · More
 *
 * The center Search button opens the same GlobalSearch palette used on desktop.
 */
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import {
  LayoutDashboard, Compass, Search, Briefcase, Menu,
  Sparkles, FlaskConical, X,
} from 'lucide-react';

const TABS = [
  { to: '/',          label: 'Home',      Icon: LayoutDashboard, end: true },
  { to: '/india',     label: 'Discover',  Icon: Compass },
  { type: 'search',   label: 'Search',    Icon: Search },
  { to: '/zerodha',   label: 'Portfolio', Icon: Briefcase },
  { type: 'more',     label: 'More',      Icon: Menu },
];

// Items shown in the "More" sheet (Intel + Tools sections).
const MORE_ITEMS = [
  { to: '/intelligence', label: 'Intelligence Hub', section: 'Intel' },
  { to: '/news',         label: 'News',             section: 'Intel' },
  { to: '/earnings',     label: 'Earnings AI',      section: 'Intel' },
  { to: '/market-breadth', label: 'Market Breadth', section: 'Intel' },
  { to: '/calendar',     label: 'Calendar',         section: 'Intel' },
  { to: '/watchlist',    label: 'Watchlist',        section: 'Stocks' },
  { to: '/chart',        label: 'Charts',           section: 'Stocks' },
  { to: '/mutual-funds', label: 'Mutual Funds',     section: 'Discover' },
  { to: '/ipo',          label: 'IPO Tracker',      section: 'Discover' },
  { to: '/agent',        label: 'Trading Agent',    section: 'Portfolio' },
  { to: '/doctor',       label: 'Portfolio Doctor', section: 'Portfolio' },
  { to: '/sip',          label: 'SIP Goals',        section: 'Portfolio' },
  { to: '/tax',          label: 'Tax Calculator',   section: 'Portfolio' },
  { to: '/chat',         label: 'Avishk AI',        section: 'Tools' },
  { to: '/backtest',     label: 'Backtest',         section: 'Tools' },
  { to: '/settings',     label: 'Settings',         section: 'Tools' },
];

function MoreSheet({ open, onClose }) {
  const navigate = useNavigate();
  if (!open) return null;

  // Group by section
  const grouped = MORE_ITEMS.reduce((acc, it) => {
    (acc[it.section] = acc[it.section] || []).push(it);
    return acc;
  }, {});

  return (
    <div className="md:hidden fixed inset-0 z-[9997]" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="absolute bottom-0 left-0 right-0 rounded-t-2xl border-t border-border max-h-[75vh] overflow-y-auto"
        style={{ background: '#0F1829' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border sticky top-0" style={{ background: '#0F1829' }}>
          <span className="text-slate-200 font-semibold">More</span>
          <button onClick={onClose} className="text-muted hover:text-slate-200 p-1"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-4 pb-8">
          {Object.entries(grouped).map(([section, items]) => (
            <div key={section}>
              <div className="text-[10px] text-muted font-semibold uppercase tracking-widest mb-2">{section}</div>
              <div className="grid grid-cols-2 gap-2">
                {items.map(it => (
                  <button
                    key={it.to}
                    onClick={() => { navigate(it.to); onClose(); }}
                    className="text-left px-3 py-2.5 rounded-lg bg-white/[0.04] border border-border text-slate-300 text-sm hover:bg-white/[0.07] transition-colors"
                  >
                    {it.label}
                  </button>
                ))}
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
      <nav
        className="md:hidden fixed bottom-0 left-0 right-0 z-[9996] border-t border-border flex items-stretch"
        style={{ background: 'rgba(10,17,32,0.97)', backdropFilter: 'blur(12px)', paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        {TABS.map(tab => {
          if (tab.type === 'search') {
            return (
              <button
                key="search"
                onClick={onSearchOpen}
                className="flex-1 flex flex-col items-center justify-center gap-0.5 py-2 relative"
              >
                <span className="w-11 h-11 -mt-5 rounded-full grid place-items-center shadow-lg"
                  style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>
                  <Search size={20} className="text-white" />
                </span>
                <span className="text-[10px] text-muted">Search</span>
              </button>
            );
          }
          if (tab.type === 'more') {
            const isActive = moreOpen;
            return (
              <button
                key="more"
                onClick={() => setMoreOpen(o => !o)}
                className="flex-1 flex flex-col items-center justify-center gap-0.5 py-2"
              >
                <tab.Icon size={20} className={isActive ? 'text-cyan' : 'text-muted'} />
                <span className={`text-[10px] ${isActive ? 'text-cyan' : 'text-muted'}`}>{tab.label}</span>
              </button>
            );
          }
          const active = tab.end ? pathname === tab.to : pathname.startsWith(tab.to);
          return (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className="flex-1 flex flex-col items-center justify-center gap-0.5 py-2"
            >
              <tab.Icon size={20} className={active ? 'text-cyan' : 'text-muted'} />
              <span className={`text-[10px] ${active ? 'text-cyan' : 'text-muted'}`}>{tab.label}</span>
            </NavLink>
          );
        })}
      </nav>
    </>
  );
}
