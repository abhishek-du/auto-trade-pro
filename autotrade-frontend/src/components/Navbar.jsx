import { useLocation } from 'react-router-dom';
import { AlertTriangle, TrendingUp, TrendingDown, Clock } from 'lucide-react';
import { usePortfolio } from '../hooks/usePortfolio';

const PAGE_TITLES = {
  '/':           'Dashboard',
  '/trades':     'Trades',
  '/analytics':  'Analytics',
  '/news':       'News',
  '/simulation': 'Simulation',
  '/settings':   'Settings',
};

function BalanceTicker({ portfolio }) {
  if (!portfolio) {
    return <span className="text-muted text-sm">Loading balance…</span>;
  }

  // WalletSummary shape: balance, realised_pnl, unrealised_pnl, roi_percent
  const balance   = portfolio.balance ?? 0;
  const change    = (portfolio.realised_pnl ?? 0) + (portfolio.unrealised_pnl ?? 0);
  const changePct = portfolio.roi_percent ?? 0;
  const positive  = change >= 0;

  return (
    <div className="flex items-center gap-3">
      <div className="text-right">
        <p className="text-slate-100 font-bold text-base tabular-nums leading-none">
          {new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(balance)}
        </p>
        <p className={`text-xs font-medium tabular-nums mt-0.5 ${positive ? 'text-profit' : 'text-loss'}`}>
          {positive ? '+' : ''}{new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(change)}
          {' '}({positive ? '+' : ''}{changePct.toFixed(2)}%)
        </p>
      </div>
      {positive
        ? <TrendingUp size={18} className="text-profit" />
        : <TrendingDown size={18} className="text-loss" />
      }
    </div>
  );
}

export default function Navbar() {
  const { pathname } = useLocation();
  const { portfolio } = usePortfolio();
  const title = PAGE_TITLES[pathname] ?? 'AutoTrade Pro';
  const now   = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  return (
    <header className="shrink-0 border-b border-border bg-panel">
      {/* Simulated money warning banner */}
      <div className="flex items-center justify-center gap-2 px-4 py-1.5 bg-warn/15 border-b border-warn/30">
        <AlertTriangle size={13} className="text-warn shrink-0" />
        <span className="text-warn text-xs font-bold tracking-wide uppercase">
          Simulated Money — Not Real Trades
        </span>
        <AlertTriangle size={13} className="text-warn shrink-0" />
      </div>

      {/* Main navbar row */}
      <div className="flex items-center justify-between px-6 py-3">
        <h1 className="text-slate-100 font-semibold text-lg">{title}</h1>

        <div className="flex items-center gap-6">
          <BalanceTicker portfolio={portfolio} />
          <div className="flex items-center gap-1.5 text-muted text-xs">
            <Clock size={12} />
            <span className="tabular-nums">{now}</span>
          </div>
        </div>
      </div>
    </header>
  );
}
