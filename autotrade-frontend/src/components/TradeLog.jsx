const fmtUSD  = (n) => '₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n ?? 0);
const fmtDate = (s) => {
  try { return new Date(s).toLocaleString('en-US', { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return '—'; }
};

export default function TradeLog({ trades = [] }) {
  if (!trades.length) return (
    <div className="rounded-xl border border-border p-10 flex flex-col items-center gap-2 glass-panel">
      <p className="text-muted text-sm">No trades recorded yet</p>
      <p className="text-muted/50 text-xs">Executed trades will appear here</p>
    </div>
  );

  return (
    <div className="rounded-xl border border-border overflow-hidden glass-panel">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Symbol', 'Dir', 'Size', 'Entry', 'Exit', 'P&L', 'Status', 'Opened'].map((h) => (
                <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {trades.map((t, i) => {
              const pnl = t.pnl ?? t.realised_pnl ?? 0;
              const pos = pnl >= 0;
              const dir = (t.direction ?? t.side ?? '').toUpperCase();
              const st  = (t.status ?? 'CLOSED').toUpperCase();
              return (
                <tr key={i} className="hover:bg-white/2 transition-colors">
                  <td className="px-4 py-3 font-semibold text-slate-100">{t.symbol}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${dir === 'BUY' ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
                      {dir === 'BUY' ? '▲' : '▼'} {dir}
                    </span>
                  </td>
                  <td className="px-4 py-3 tabular-nums text-slate-300">{fmtUSD(t.position_size ?? t.size)}</td>
                  <td className="px-4 py-3 tabular-nums text-slate-300">{fmtUSD(t.entry_price)}</td>
                  <td className="px-4 py-3 tabular-nums text-slate-300">{t.exit_price ? fmtUSD(t.exit_price) : '—'}</td>
                  <td className="px-4 py-3 tabular-nums font-semibold">
                    <span className={pos ? 'text-profit' : 'text-loss'}>{pos ? '+' : ''}{fmtUSD(pnl)}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase border ${
                      st === 'OPEN'    ? 'border-cyan/25 text-cyan bg-cyan/8' :
                      st === 'STOPPED' ? 'border-warn/25 text-warn bg-warn/8' :
                      'border-border text-muted bg-transparent'}`}>
                      {st}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted text-xs">{fmtDate(t.opened_at ?? t.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
