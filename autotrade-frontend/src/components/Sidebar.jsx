import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, ArrowLeftRight, BarChart2,
  Newspaper, FlaskConical, Settings, TrendingUp, BookOpenText,
} from 'lucide-react';

const NAV_ITEMS = [
  { to: '/',           label: 'Dashboard',  Icon: LayoutDashboard },
  { to: '/trades',     label: 'Trades',     Icon: ArrowLeftRight  },
  { to: '/analytics',  label: 'Analytics',  Icon: BarChart2       },
  { to: '/news',       label: 'News',       Icon: Newspaper       },
  { to: '/simulation', label: 'Simulation', Icon: FlaskConical    },
  { to: '/settings',   label: 'Settings',   Icon: Settings        },
  { to: '/documentation', label: 'Documentation', Icon: BookOpenText },
];

export default function Sidebar() {
  return (
    <aside className="flex flex-col w-60 shrink-0 h-screen border-r border-border"
      style={{ background: 'linear-gradient(180deg,#0A1120 0%,#080D1A 100%)' }}>

      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-border">
        <div className="p-2 rounded-xl" style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>
          <TrendingUp size={18} className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-slate-300 font-bold text-sm tracking-wide">AutoTrade</div>
          <div className="gradient-text font-extrabold text-base leading-none">Pro</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        <p className="px-3 pt-1 pb-2.5 text-[10px] font-semibold uppercase tracking-widest text-muted">
          Menu
        </p>
        {NAV_ITEMS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) => [
              'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150',
              isActive ? 'text-white border border-accent/20' : 'text-muted hover:text-slate-200 hover:bg-white/5',
            ].join(' ')}
            style={({ isActive }) => isActive
              ? { background: 'linear-gradient(135deg,rgba(59,130,246,0.15),rgba(6,182,212,0.08))' }
              : {}}
          >
            {({ isActive }) => (
              <>
                <Icon size={16} className={isActive ? 'text-cyan' : ''} />
                {label}
                {isActive && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-cyan shrink-0" />}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Paper Mode badge */}
      <div className="px-4 py-4 border-t border-border">
        <div className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl border border-warn/20"
          style={{ background: 'rgba(245,158,11,0.06)' }}>
          <span className="pulse-dot w-2 h-2 rounded-full bg-warn shrink-0" />
          <div>
            <p className="text-warn font-bold text-[11px] tracking-widest uppercase">Paper Mode</p>
            <p className="text-warn/55 text-[10px] mt-0.5">Virtual currency only</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
