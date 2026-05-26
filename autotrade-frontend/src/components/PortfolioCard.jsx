const fmtUSD = (n) =>
  ('₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n ?? 0));

export default function PortfolioCard({ portfolio }) {
  if (!portfolio) return null;
  const { balance = 0, equity = 0, realised_pnl = 0, unrealised_pnl = 0, roi_percent = 0, win_rate = 0 } = portfolio;
  const totalPnl = realised_pnl + unrealised_pnl;
  const up = totalPnl >= 0;

  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="px-5 py-4 border-b border-border flex items-center justify-between">
        <h3 className="text-slate-100 font-semibold text-sm">Portfolio Summary</h3>
        <span className={`text-xs font-bold tabular-nums ${up ? 'text-profit' : 'text-loss'}`}>
          {up ? '+' : ''}{roi_percent.toFixed(2)}% ROI
        </span>
      </div>
      <div className="grid grid-cols-2 divide-x divide-border">
        {[
          { label: 'Balance',         value: fmtUSD(balance),        cls: 'text-slate-100' },
          { label: 'Equity',          value: fmtUSD(equity),         cls: 'text-slate-100' },
          { label: 'Realised P&L',    value: fmtUSD(realised_pnl),   cls: realised_pnl   >= 0 ? 'text-profit' : 'text-loss' },
          { label: 'Unrealised P&L',  value: fmtUSD(unrealised_pnl), cls: unrealised_pnl >= 0 ? 'text-profit' : 'text-loss' },
          { label: 'Win Rate',        value: `${win_rate.toFixed(1)}%`, cls: 'text-cyan'  },
          { label: 'Total P&L',       value: fmtUSD(totalPnl),       cls: up ? 'text-profit' : 'text-loss' },
        ].map(({ label, value, cls }, i) => (
          <div key={i} className={`px-5 py-3.5 border-b border-border last:border-b-0 ${i % 2 === 1 ? '' : ''}`}>
            <p className="text-muted text-[11px] uppercase tracking-wider mb-1">{label}</p>
            <p className={`font-bold tabular-nums ${cls}`}>{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
