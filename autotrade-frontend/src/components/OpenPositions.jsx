import { formatINR } from '../utils/indianFormat';
const fmtUSD = (n) => formatINR(n ?? 0);

export default function OpenPositions({ positions = [] }) {
  return (
    <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
        <h2 className="text-slate-100 font-semibold text-sm">Open Positions</h2>
        <span className="px-2 py-0.5 text-xs font-bold rounded-full border border-accent/25 text-accent"
          style={{ background: 'rgba(59,130,246,0.1)' }}>
          {positions.length} active
        </span>
      </div>

      {positions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 gap-2">
          <div className="w-10 h-10 rounded-full border border-border flex items-center justify-center mb-1">
            <span className="text-muted text-lg">—</span>
          </div>
          <p className="text-muted text-sm">No open positions</p>
          <p className="text-muted/60 text-xs">Waiting for trading signals…</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Symbol', 'Dir', 'Size', 'Entry', 'Current', 'P&L', 'SL', 'TP'].map((h) => (
                  <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {positions.map((p, i) => {
                const pnl = p.unrealised_pnl ?? p.pnl ?? 0;
                const pos = pnl >= 0;
                const dir = (p.direction ?? p.side ?? '').toUpperCase();
                return (
                  <tr key={i} className="hover:bg-white/2 transition-colors">
                    <td className="px-4 py-3 font-semibold text-slate-100">{p.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold ${dir === 'BUY' ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
                        {dir === 'BUY' ? '▲ BUY' : '▼ SELL'}
                      </span>
                    </td>
                    <td className="px-4 py-3 tabular-nums text-slate-300">{fmtUSD(p.position_size ?? p.size)}</td>
                    <td className="px-4 py-3 tabular-nums text-slate-300">{fmtUSD(p.entry_price)}</td>
                    <td className="px-4 py-3 tabular-nums text-slate-300">{fmtUSD(p.current_price)}</td>
                    <td className="px-4 py-3 tabular-nums font-semibold">
                      <span className={pos ? 'text-profit' : 'text-loss'}>{pos ? '+' : ''}{fmtUSD(pnl)}</span>
                    </td>
                    <td className="px-4 py-3 tabular-nums text-loss/70 text-xs">{fmtUSD(p.stop_loss)}</td>
                    <td className="px-4 py-3 tabular-nums text-profit/70 text-xs">{fmtUSD(p.take_profit)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
