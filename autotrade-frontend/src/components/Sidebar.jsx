import { NavLink } from 'react-router-dom';
import { useState, useEffect } from 'react';
import {
  LayoutDashboard, ArrowLeftRight, BarChart2,
  Newspaper, FlaskConical, Settings, TrendingUp, BookOpenText,
  Globe, Zap, Wallet, LineChart, TestTube2, Briefcase, Radio, BookMarked,
  CandlestickChart as ChartIcon, Activity, LayoutGrid, CalendarDays, IndianRupee, Target, Receipt, Rocket,
  Bot, Stethoscope, FileText, BrainCircuit, Sparkles,
} from 'lucide-react';
import { getZerodhaStatus, getIndiaMarketStatus, getWatchlist } from '../api/client';

const MAIN_NAV = [
  { to: '/',            label: 'Dashboard',     Icon: LayoutDashboard },
  { to: '/trades',      label: 'Trades',        Icon: ArrowLeftRight  },
  { to: '/analytics',   label: 'Analytics',     Icon: BarChart2       },
  { to: '/news',        label: 'News',          Icon: Newspaper       },
  { to: '/simulation',  label: 'Simulation',    Icon: FlaskConical    },
  { to: '/settings',    label: 'Settings',      Icon: Settings        },
  { to: '/documentation', label: 'Documentation', Icon: BookOpenText  },
];

const INDIA_NAV = [
  { to: '/live-market',     label: 'Live Market',    Icon: Radio,       liveMarket: true  },
  { to: '/watchlist',       label: 'Watchlist',      Icon: BookMarked,  watchlist: true   },
  { to: '/chart',           label: 'Charts',         Icon: ChartIcon  },
  { to: '/market-breadth',  label: 'Breadth',        Icon: Activity,    breadth: true      },
  { to: '/sector-heatmap', label: 'Sector Heatmap', Icon: LayoutGrid,  sectorHeatmap: true },
  { to: '/portfolio-tracker', label: 'My Portfolio',    Icon: Briefcase,   portfolioTracker: true },
  { to: '/doctor',           label: 'Portfolio Doctor', Icon: Stethoscope, doctorBadge: true },
  { to: '/earnings',         label: 'Earnings AI',      Icon: FileText,    earningsBadge: true },
  { to: '/intelligence',     label: 'Intelligence Hub', Icon: Sparkles,    hubBadge: true },
  { to: '/agent',            label: 'Trading Agent',    Icon: BrainCircuit, agentBadge: true },
  { to: '/calendar',          label: 'Market Calendar', Icon: CalendarDays, calendar: true },
  { to: '/india',           label: 'India Overview', Icon: Globe      },
  { to: '/india/signals',   label: 'NSE Signals',    Icon: Zap        },
  { to: '/zerodha',         label: 'Zerodha',        Icon: Zap,         zerodha: true     },
  { to: '/portfolio',       label: 'Simulator',      Icon: FlaskConical },
  { to: '/mutual-funds',    label: 'Mutual Funds',   Icon: Wallet     },
  { to: '/sip',             label: 'SIP Goals',      Icon: Target     },
  { to: '/tax',             label: 'Tax Calculator',    Icon: Receipt,   },
  { to: '/allocation',      label: 'Asset Allocation',  Icon: IndianRupee, allocation: true },
  { to: '/ipo',            label: 'IPO Tracker',       Icon: Rocket,      ipoTracker: true },
  { to: '/fundamentals',    label: 'Fundamentals',   Icon: LineChart  },
  { to: '/backtest',        label: 'Backtest',       Icon: TestTube2  },
];

function NavItem({ to, label, Icon, end }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => [
        'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150',
        isActive
          ? 'text-white border border-accent/20'
          : 'text-muted hover:text-slate-200 hover:bg-white/5',
      ].join(' ')}
      style={({ isActive }) =>
        isActive
          ? { background: 'linear-gradient(135deg,rgba(59,130,246,0.15),rgba(6,182,212,0.08))' }
          : {}
      }
    >
      {({ isActive }) => (
        <>
          <Icon size={16} className={isActive ? 'text-cyan' : ''} />
          {label}
          {isActive && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-cyan shrink-0" />}
        </>
      )}
    </NavLink>
  );
}

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
      fetch('/api/v1/india/sectors/summary')
        .then(r => r.json())
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
      fetch('/api/v1/india/breadth/summary')
        .then(r => r.json())
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
      fetch('/api/v1/india/calendar/upcoming?days=7')
        .then(r => r.json())
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
      fetch('/api/v1/portfolios/')
        .then(r => r.json())
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
      fetch('/api/v1/portfolios/')
        .then(r => r.json())
        .then(portfolios => {
          if (!Array.isArray(portfolios) || portfolios.length === 0) { setDotColor(null); return; }
          const pid = portfolios[0]?.id;
          if (!pid) return;
          return fetch(`/api/v1/allocation/analysis?portfolio_id=${pid}&risk_profile=moderate`)
            .then(r => r.json())
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
      fetch('/api/v1/intelligence/context')
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
      fetch('/api/v1/agent/status')
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
      fetch('/api/v1/earnings/recent?limit=10')
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
      fetch('/api/v1/portfolios/')
        .then(r => r.json())
        .then(portfolios => {
          if (!Array.isArray(portfolios) || portfolios.length === 0) { setInfo(null); return; }
          const pid = portfolios[0]?.id;
          if (!pid) return;
          return fetch(`/api/v1/doctor/diagnose/${pid}`)
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
      fetch('/api/v1/ipo/stats/summary')
        .then(r => r.json())
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
          const t = await fetch('/api/v1/zerodha/ticker/status').then(r => r.json());
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

export default function Sidebar() {
  return (
    <aside
      className="flex flex-col w-60 shrink-0 h-screen border-r border-border"
      style={{ background: 'linear-gradient(180deg,#0A1120 0%,#080D1A 100%)' }}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-border">
        <div className="p-2 rounded-xl"
          style={{ background: 'linear-gradient(135deg,#1D4ED8,#0891B2)' }}>
          <TrendingUp size={18} className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-slate-300 font-bold text-sm tracking-wide">AutoTrade</div>
          <div className="gradient-text font-extrabold text-base leading-none">Pro</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">

        {/* Avishk AI — top of nav */}
        <NavLink
          to="/chat"
          className={({ isActive }) => [
            'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150 mb-2',
            isActive
              ? 'text-white border border-accent/40'
              : 'text-slate-300 border border-accent/20 hover:border-accent/40 hover:text-white',
          ].join(' ')}
          style={({ isActive }) => ({
            background: isActive
              ? 'linear-gradient(135deg,rgba(29,78,216,0.25),rgba(8,145,178,0.15))'
              : 'linear-gradient(135deg,rgba(29,78,216,0.1),rgba(8,145,178,0.06))',
          })}
        >
          {({ isActive }) => (
            <>
              <Bot size={16} className={isActive ? 'text-cyan' : 'text-accent'} />
              Avishk AI Analyst
              <span className="ml-auto flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-profit shrink-0 arjun-pulse" />
              </span>
            </>
          )}
        </NavLink>

        {/* Main section */}
        <p className="px-3 pt-1 pb-2.5 text-[10px] font-semibold uppercase tracking-widest text-muted">
          Menu
        </p>
        {MAIN_NAV.map(({ to, label, Icon }) => (
          <NavItem key={to} to={to} label={label} Icon={Icon} end={to === '/'} />
        ))}

        {/* Indian Market section */}
        <p className="px-3 pt-5 pb-2.5 text-[10px] font-semibold uppercase tracking-widest text-muted">
          Indian Market
        </p>
        {INDIA_NAV.map(({ to, label, Icon, zerodha: isZerodha, liveMarket: isLiveMarket, watchlist: isWatchlist, breadth: isBreadth, sectorHeatmap: isSectorHeatmap, calendar: isCalendar, portfolioTracker: isPortfolioTracker, allocation: isAllocation, ipoTracker: isIPOTracker, doctorBadge: isDoctorBadge, earningsBadge: isEarningsBadge, agentBadge: isAgentBadge, hubBadge: isHubBadge }) => {
          if (isZerodha || isLiveMarket || isWatchlist || isBreadth || isSectorHeatmap || isCalendar || isPortfolioTracker || isAllocation || isIPOTracker || isDoctorBadge || isEarningsBadge || isAgentBadge || isHubBadge) {
            const Dot = isZerodha ? ZerodhaDot : isLiveMarket ? MarketDot : isWatchlist ? WatchlistBadge : isBreadth ? BreadthDot : isSectorHeatmap ? SectorStrip : isCalendar ? CalendarBadge : isAllocation ? AllocationDot : isIPOTracker ? IPOBadge : isDoctorBadge ? DoctorHealthBadge : isEarningsBadge ? EarningsBadge : isAgentBadge ? AgentStatusBadge : isHubBadge ? HubBiasBadge : PortfolioValueBadge;
            return (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) => [
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150',
                  isActive
                    ? 'text-white border border-accent/20'
                    : 'text-muted hover:text-slate-200 hover:bg-white/5',
                ].join(' ')}
                style={({ isActive }) =>
                  isActive
                    ? { background: 'linear-gradient(135deg,rgba(59,130,246,0.15),rgba(6,182,212,0.08))' }
                    : {}
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon size={16} className={isActive ? 'text-cyan' : ''} />
                    {label}
                    <Dot />
                  </>
                )}
              </NavLink>
            );
          }
          return <NavItem key={to} to={to} label={label} Icon={Icon} end={to === '/india'} />;
        })}
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
