import { NavLink, useLocation } from 'react-router-dom';
import { useState, useEffect } from 'react';
import {
  LayoutDashboard, ArrowLeftRight, BarChart2,
  Newspaper, FlaskConical, Settings, TrendingUp, BookOpenText,
  Globe, Zap, Wallet, LineChart, TestTube2, Briefcase, Radio, BookMarked,
  CandlestickChart as ChartIcon, Activity, LayoutGrid, CalendarDays, IndianRupee, Target, Receipt, Rocket,
  Bot, Stethoscope, FileText, BrainCircuit, Sparkles, ChevronDown, Compass, ClipboardList, Scale, Layers,
} from 'lucide-react';
import { getZerodhaStatus, getIndiaMarketStatus, getWatchlist, apiFetch } from '../api/client';

// ── 6-section information architecture ────────────────────────────────────────
// Collapses ~25 routes into 6 collapsible groups: Terminal · Discover · Stocks ·
// Portfolio · Intel · Tools.  `badge` keys map to the live status components
// defined below (see BADGE_MAP).
export const SECTIONS = [
  {
    key: 'terminal', label: 'Terminal', Icon: LayoutDashboard,
    items: [
      { to: '/', label: 'Dashboard', Icon: LayoutDashboard, end: true },
    ],
  },
  {
    key: 'discover', label: 'Discover', Icon: Compass,
    items: [
      { to: '/discover/scanner', label: 'Market Scanner', Icon: Zap },
      { to: '/india',            label: 'India Overview', Icon: Globe },
      { to: '/india/signals',    label: 'NSE Signals',    Icon: Target },
      { to: '/fno',              label: 'Futures & Options', Icon: Layers },
      { to: '/fundamentals',     label: 'Screener',       Icon: LineChart },
      { to: '/sector-heatmap',   label: 'Sector Heatmap', Icon: LayoutGrid, badge: 'sectorHeatmap' },
      { to: '/mutual-funds',     label: 'Mutual Funds',   Icon: Wallet },
      { to: '/ipo',              label: 'IPO Tracker',    Icon: Rocket, badge: 'ipoTracker' },
    ],
  },
  {
    key: 'stocks', label: 'Stocks', Icon: ChartIcon,
    items: [
      { to: '/watchlist',   label: 'Watchlist',   Icon: BookMarked, badge: 'watchlist' },
      { to: '/chart',       label: 'Charts',      Icon: ChartIcon },
      { to: '/live-market', label: 'Live Market', Icon: Radio, badge: 'liveMarket' },
    ],
  },
  {
    key: 'portfolio', label: 'Portfolio', Icon: Briefcase,
    items: [
      { to: '/zerodha',     label: 'Portfolio',        Icon: Briefcase, badge: 'zerodha' },
      { to: '/agent',       label: 'Trading Agent',    Icon: BrainCircuit, badge: 'agentBadge' },
      { to: '/doctor',      label: 'Portfolio Doctor', Icon: Stethoscope, badge: 'doctorBadge' },
      { to: '/portfolio-analytics', label: 'Capital Model',     Icon: Scale },
      { to: '/allocation',  label: 'Asset Allocation', Icon: IndianRupee, badge: 'allocation' },
      { to: '/sip',         label: 'SIP Goals',        Icon: Target },
      { to: '/tax',         label: 'Tax Calculator',   Icon: Receipt },
    ],
  },
  {
    key: 'intel', label: 'Intel', Icon: Sparkles,
    items: [
      { to: '/intelligence',   label: 'Intelligence Hub', Icon: Sparkles, badge: 'hubBadge' },
      { to: '/agent-log',      label: 'Agent Log',        Icon: ClipboardList },
      { to: '/news',           label: 'News',             Icon: Newspaper },
      { to: '/earnings',       label: 'Earnings AI',      Icon: FileText, badge: 'earningsBadge' },
      { to: '/market-breadth', label: 'Market Breadth',   Icon: Activity, badge: 'breadth' },
      { to: '/calendar',       label: 'Calendar',         Icon: CalendarDays, badge: 'calendar' },
    ],
  },
  {
    key: 'tools', label: 'Tools', Icon: FlaskConical,
    items: [
      { to: '/chat',          label: 'Avishk AI Analyst', Icon: Bot },
      { to: '/backtest',      label: 'Backtest',          Icon: TestTube2 },
      { to: '/trades',        label: 'Trades',            Icon: ArrowLeftRight },
      { to: '/analytics',     label: 'Analytics',         Icon: BarChart2 },
      { to: '/simulation',    label: 'Simulation',        Icon: FlaskConical },
      { to: '/settings',      label: 'Settings',          Icon: Settings },
      { to: '/documentation', label: 'Documentation',     Icon: BookOpenText },
    ],
  },
];

function MarketDot() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    const check = () =>
      getIndiaMarketStatus()
        .then(s => setStatus(s?.market_open ? 'OPEN' : 'CLOSED'))
        .catch(() => setStatus('CLOSED'));
    check();
    const id = setInterval(check, 60_000);
    return () => clearInterval(id);
  }, []);
  if (status === null) return null;
  const isOpen = status === 'OPEN';
  return (
    <span className={`ml-auto w-2 h-2 rounded-full shrink-0 ${isOpen ? 'bg-profit animate-pulse' : 'bg-loss'}`} />
  );
}

function WatchlistBadge() {
  const [count, setCount] = useState(null);
  useEffect(() => {
    const load = () =>
      getWatchlist()
        .then(data => {
          const n = (data.stocks || []).filter(s => s.signal === 'BUY').length;
          setCount(n > 0 ? n : null);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);
  if (!count) return null;
  return (
    <span className="ml-auto text-[10px] font-bold bg-profit/20 text-profit px-1.5 py-0.5 rounded-full shrink-0">
      {count}
    </span>
  );
}

function SectorStrip() {
  const [sectors, setSectors] = useState([]);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/india/sectors/summary')
        .then(d => setSectors(Array.isArray(d) ? d.slice(0, 4) : []))
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);
  if (!sectors.length) return null;
  // Inline color lookup (can't import utils in sidebar easily — simple version)
  const dotColor = (pct) => {
    if (pct == null) return '#475569';
    if (pct >= 2)  return '#14532D';
    if (pct > 0)   return '#15803D';
    if (pct > -2)  return '#B91C1C';
    return '#7F1D1D';
  };
  return (
    <div className="ml-auto flex items-center gap-0.5 shrink-0">
      {sectors.map(s => (
        <div key={s.sector_key} title={`${s.short}: ${s.avg_change_pct > 0 ? '+' : ''}${s.avg_change_pct?.toFixed(1)}%`}
          style={{ width: 4, height: 14, background: dotColor(s.avg_change_pct), borderRadius: 2 }} />
      ))}
    </div>
  );
}

function BreadthDot() {
  const [mood, setMood] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/india/breadth/summary')
        .then(d => setMood(d?.nse_market_mood || null))
        .catch(() => {});
    load();
    const id = setInterval(load, 120_000);
    return () => clearInterval(id);
  }, []);
  if (!mood) return null;
  const cls =
    mood === 'STRONGLY_BULLISH' || mood === 'BULLISH'   ? 'bg-profit' :
    mood === 'STRONGLY_BEARISH' || mood === 'BEARISH'   ? 'bg-loss'   : 'bg-slate-500';
  return <span className={`ml-auto w-2 h-2 rounded-full shrink-0 ${cls}`} />;
}

function CalendarBadge() {
  const [count, setCount] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/india/calendar/upcoming?days=7')
        .then(d => {
          const n = (d.events || []).length;
          setCount(n > 0 ? n : null);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 300_000);
    return () => clearInterval(id);
  }, []);
  if (!count) return null;
  return (
    <span className="ml-auto text-[10px] font-bold bg-cyan/20 text-cyan px-1.5 py-0.5 rounded-full shrink-0">
      {count}
    </span>
  );
}

function PortfolioValueBadge() {
  const [value, setValue] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/portfolios/')
        .then(portfolios => {
          if (!Array.isArray(portfolios) || portfolios.length === 0) { setValue(null); return; }
          const total = portfolios.reduce((s, p) => s + (p.summary?.current_value || 0), 0);
          setValue(total > 0 ? total : null);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);
  if (!value) return null;
  const fmt = value >= 100000 ? '₹' + (value / 100000).toFixed(1) + 'L' : '₹' + Math.round(value / 1000) + 'K';
  return (
    <span className="ml-auto text-[10px] font-bold bg-profit/15 text-profit px-1.5 py-0.5 rounded-full shrink-0">
      {fmt}
    </span>
  );
}

function AllocationDot() {
  const [dotColor, setDotColor] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/portfolios/')
        .then(portfolios => {
          if (!Array.isArray(portfolios) || portfolios.length === 0) { setDotColor(null); return; }
          const pid = portfolios[0]?.id;
          if (!pid) return;
          return apiFetch(`/api/v1/allocation/analysis?portfolio_id=${pid}&risk_profile=moderate`)
            .then(d => {
              const maxDev = (d.rebalancing || [])
                .filter(r => r.action !== 'HOLD')
                .reduce((m, r) => Math.max(m, Math.abs(r.deviation_pct)), 0);
              if (maxDev > 10) setDotColor('#EF4444');
              else if (maxDev > 5) setDotColor('#F59E0B');
              else setDotColor('#10B981');
            });
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 300_000);
    return () => clearInterval(id);
  }, []);
  if (!dotColor) return null;
  return <span className="ml-auto w-2 h-2 rounded-full shrink-0" style={{ background: dotColor }} />;
}

function HubBiasBadge() {
  const [info, setInfo] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/intelligence/context')
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.macro) setInfo({ bias: d.macro.total_macro_bias ?? 0 }); })
        .catch(() => {});
    load();
    const id = setInterval(load, 120_000);
    return () => clearInterval(id);
  }, []);
  if (!info) return null;
  const b = info.bias;
  const cls = b > 0 ? 'bg-emerald-500/20 text-emerald-400' : b < 0 ? 'bg-red-500/20 text-red-400' : 'bg-slate-500/20 text-slate-300';
  return (
    <span className={`ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded-full shrink-0 ${cls}`}
      title={`Macro bias ${b > 0 ? '+' : ''}${b}`}>
      {b > 0 ? '+' : ''}{b}
    </span>
  );
}

function AgentStatusBadge() {
  const [info, setInfo] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/agent/status')
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setInfo({ enabled: d.enabled, paper: d.paper_mode, positions: d.portfolio?.open_positions_count || 0 }); })
        .catch(() => {});
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);
  if (!info) return null;
  if (info.enabled) {
    return (
      <span className="ml-auto flex items-center gap-1 shrink-0">
        {info.positions > 0 && <span className="text-[10px] font-bold text-cyan/80 tabular-nums">{info.positions}</span>}
        <span className={`w-1.5 h-1.5 rounded-full ${info.paper ? 'bg-blue-400' : 'bg-emerald-400 animate-pulse'}`} title={info.paper ? 'Paper mode' : 'LIVE'} />
      </span>
    );
  }
  return <span className="ml-auto w-1.5 h-1.5 rounded-full bg-slate-500 shrink-0" title="Agent disabled" />;
}

function EarningsBadge() {
  const [count, setCount] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/earnings/recent?limit=10')
        .then(r => r.ok ? r.json() : [])
        .then(d => {
          const n = Array.isArray(d) ? d.length : 0;
          setCount(n > 0 ? n : null);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 3600_000); // hourly
    return () => clearInterval(id);
  }, []);
  if (!count) return null;
  return (
    <span className="ml-auto text-[10px] font-bold bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded-full shrink-0">
      {count}
    </span>
  );
}

function DoctorHealthBadge() {
  const [info, setInfo] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/portfolios/')
        .then(portfolios => {
          if (!Array.isArray(portfolios) || portfolios.length === 0) { setInfo(null); return; }
          const pid = portfolios[0]?.id;
          if (!pid) return;
          return apiFetch(`/api/v1/doctor/diagnose/${pid}`)
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d) setInfo({ score: d.overall_score, grade: d.overall_grade, critical: (d.findings || []).filter(f => f.severity === 'CRITICAL').length }); });
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 300_000);
    return () => clearInterval(id);
  }, []);
  if (!info) return null;
  const color = info.score >= 85 ? '#10B981' : info.score >= 70 ? '#22D3EE' : info.score >= 55 ? '#F59E0B' : info.score >= 40 ? '#F97316' : '#EF4444';
  return (
    <span className="ml-auto flex items-center gap-1 shrink-0">
      {info.critical > 0 && <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse shrink-0" />}
      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full border" style={{ color, borderColor: color + '40', background: color + '15' }}>{info.grade}</span>
    </span>
  );
}

function IPOBadge() {
  const [count, setCount] = useState(null);
  useEffect(() => {
    const load = () =>
      apiFetch('/api/v1/ipo/stats/summary')
        .then(d => {
          const n = d?.by_status?.open ?? 0;
          setCount(n > 0 ? n : null);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 300_000);
    return () => clearInterval(id);
  }, []);
  if (!count) return null;
  return (
    <span className="ml-auto text-[10px] font-bold bg-profit/20 text-profit px-1.5 py-0.5 rounded-full shrink-0">
      {count}
    </span>
  );
}

function ZerodhaDot() {
  // Status states: null (loading) → { connected, paper_mode, ticker_running, cash }
  const [info, setInfo] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const s = await getZerodhaStatus();
        if (cancelled) return;
        // Best-effort: ticker status (separate endpoint, fast)
        let ticker = false;
        try {
          const t = await apiFetch('/api/v1/zerodha/ticker/status');
          ticker = Boolean(t?.running);
        } catch { /* ignore */ }
        if (cancelled) return;
        setInfo({
          connected:      Boolean(s?.connected),
          paper_mode:     Boolean(s?.paper_mode ?? true),
          ticker_running: ticker,
          cash:           Number(s?.available_margins_inr ?? 0),
        });
      } catch {
        if (!cancelled) setInfo({ connected: false, paper_mode: true, ticker_running: false, cash: 0 });
      }
    };
    load();
    const id = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (info === null) return null;

  // Color logic per spec:
  //   not connected         → amber
  //   connected + paper     → blue
  //   connected + real      → green pulsing
  let dotCls = 'bg-amber-400';
  if (info.connected) {
    dotCls = info.paper_mode ? 'bg-blue-400' : 'bg-emerald-400 animate-pulse';
  }

  // Cash badge (only when connected & non-zero)
  const cashLabel = info.connected && info.cash > 0
    ? (info.cash >= 100_000
        ? '₹' + (info.cash / 100_000).toFixed(1) + 'L'
        : '₹' + Math.round(info.cash / 1000) + 'K')
    : null;

  return (
    <span className="ml-auto flex items-center gap-1 shrink-0">
      {cashLabel && (
        <span className="text-[10px] font-bold text-slate-400/80 tabular-nums">{cashLabel}</span>
      )}
      <span
        className={`w-2 h-2 rounded-full ${dotCls}`}
        title={
          !info.connected ? 'Zerodha not connected' :
          info.paper_mode ? 'Connected (paper mode)' :
          info.ticker_running ? 'LIVE — real orders enabled' :
          'Connected — real orders enabled'
        }
      />
    </span>
  );
}

// Maps a nav item's `badge` key to its live status component.
const BADGE_MAP = {
  zerodha:       ZerodhaDot,
  liveMarket:    MarketDot,
  watchlist:     WatchlistBadge,
  breadth:       BreadthDot,
  sectorHeatmap: SectorStrip,
  calendar:      CalendarBadge,
  allocation:    AllocationDot,
  ipoTracker:    IPOBadge,
  doctorBadge:   DoctorHealthBadge,
  earningsBadge: EarningsBadge,
  agentBadge:    AgentStatusBadge,
  hubBadge:      HubBiasBadge,
};

// ── One nav row inside a section ──────────────────────────────────────────────
function SectionItem({ to, label, Icon, badge, end }) {
  const Dot = badge ? BADGE_MAP[badge] : null;
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => [
        'flex items-center gap-3 pl-9 pr-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150',
        isActive ? 'text-white' : 'text-muted hover:text-slate-200 hover:bg-white/5',
      ].join(' ')}
      style={({ isActive }) =>
        isActive
          ? { background: 'linear-gradient(135deg,rgba(59,130,246,0.15),rgba(6,182,212,0.08))' }
          : {}
      }
    >
      {({ isActive }) => (
        <>
          <Icon size={15} className={isActive ? 'text-cyan' : ''} />
          <span className="flex-1">{label}</span>
          {Dot && <Dot />}
          {isActive && !Dot && <span className="w-1.5 h-1.5 rounded-full bg-cyan shrink-0" />}
        </>
      )}
    </NavLink>
  );
}

// ── Collapsible section group ─────────────────────────────────────────────────
function SectionGroup({ section, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  // Keep the active section expanded if the route changes into it.
  useEffect(() => { if (defaultOpen) setOpen(true); }, [defaultOpen]);

  const { label, Icon, items } = section;
  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-300 hover:text-white hover:bg-white/5 transition-all duration-150"
      >
        <Icon size={16} className={open ? 'text-cyan' : 'text-muted'} />
        <span className="flex-1 text-left">{label}</span>
        <ChevronDown size={14} className={`text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="mt-0.5 space-y-0.5 pb-1">
          {items.map(item => <SectionItem key={item.to} {...item} />)}
        </div>
      )}
    </div>
  );
}

export default function Sidebar() {
  const { pathname } = useLocation();

  // Determine which section owns the current route so it auto-expands.
  const activeKey = SECTIONS.find(s =>
    s.items.some(i => i.end ? pathname === i.to : pathname.startsWith(i.to) && i.to !== '/')
  )?.key ?? (pathname === '/' ? 'terminal' : null);

  return (
    <aside
      className="hidden md:flex flex-col w-60 shrink-0 h-screen border-r border-border"
      style={{ background: 'linear-gradient(180deg,#0A1120 0%,#080D1A 100%)' }}
    >
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

      {/* Navigation — 6 collapsible sections */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        {SECTIONS.map(section => (
          <SectionGroup
            key={section.key}
            section={section}
            defaultOpen={section.key === activeKey || section.key === 'terminal'}
          />
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
