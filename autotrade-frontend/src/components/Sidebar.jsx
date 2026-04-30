import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  ArrowLeftRight,
  BarChart2,
  Newspaper,
  FlaskConical,
  Settings,
  TrendingUp,
} from 'lucide-react';

const NAV_ITEMS = [
  { to: '/',           label: 'Dashboard',  Icon: LayoutDashboard },
  { to: '/trades',     label: 'Trades',     Icon: ArrowLeftRight  },
  { to: '/analytics',  label: 'Analytics',  Icon: BarChart2       },
  { to: '/news',       label: 'News',       Icon: Newspaper       },
  { to: '/simulation', label: 'Simulation', Icon: FlaskConical    },
  { to: '/settings',   label: 'Settings',   Icon: Settings        },
];

export default function Sidebar() {
  return (
    <aside className="flex flex-col w-60 shrink-0 bg-panel border-r border-border h-screen">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-border">
        <div className="p-1.5 bg-accent/20 rounded-lg">
          <TrendingUp size={20} className="text-accent" />
        </div>
        <span className="text-slate-100 font-bold text-base leading-tight">
          AutoTrade<br />
          <span className="text-accent font-extrabold">Pro</span>
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        {NAV_ITEMS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              [
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all',
                isActive
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'text-muted hover:bg-surface hover:text-slate-100',
              ].join(' ')
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Paper Mode badge */}
      <div className="px-4 py-4 border-t border-border">
        <div className="flex items-center gap-2.5 px-3 py-3 bg-warn/10 border border-warn/30 rounded-lg">
          <span
            className="pulse-dot w-2.5 h-2.5 rounded-full bg-warn shrink-0"
            aria-hidden="true"
          />
          <div className="flex flex-col leading-tight">
            <span className="text-warn font-bold text-xs tracking-widest uppercase">
              Paper Mode
            </span>
            <span className="text-warn/70 text-[10px]">Simulation running</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
