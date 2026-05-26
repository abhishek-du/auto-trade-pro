import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';

const fmtUSD = (n) => '₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n ?? 0);

function MiniTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border px-3 py-2 text-xs" style={{ background: '#131E30' }}>
      <p className="text-muted mb-1">{label}</p>
      <p className="text-slate-100 font-bold">{fmtUSD(payload[0]?.value)}</p>
    </div>
  );
}

export default function AnalyticsPanel({ data }) {
  const equity = data?.equity_curve ?? [];
  const minEq  = Math.min(...equity.map((d) => d.equity ?? 1000)) * 0.997;
  const maxEq  = Math.max(...equity.map((d) => d.equity ?? 1000)) * 1.003;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
        <div className="px-5 py-3.5 border-b border-border">
          <h3 className="text-slate-100 font-semibold text-sm">Equity Curve</h3>
        </div>
        <div className="h-48 px-2 py-3">
          {equity.length === 0 ? (
            <div className="h-full flex items-center justify-center">
              <p className="text-muted text-sm">No data yet — trades needed</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equity} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="analyticsGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3B82F6" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#3B82F6" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2D45" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#4E6280', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis domain={[minEq, maxEq]} tick={{ fill: '#4E6280', fontSize: 10 }} axisLine={false} tickLine={false}
                  tickFormatter={(v) => `$${v.toFixed(0)}`} width={52} />
                <Tooltip content={<MiniTooltip />} />
                <Area type="monotone" dataKey="equity" stroke="#3B82F6" strokeWidth={2}
                  fill="url(#analyticsGrad)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Win Rate',     value: `${(data?.win_rate ?? 0).toFixed(1)}%`,  cls: 'text-profit' },
          { label: 'Total Trades', value: data?.total_trades ?? 0,                 cls: 'text-slate-100' },
          { label: 'Total P&L',    value: fmtUSD(data?.total_pnl),                cls: (data?.total_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss' },
          { label: 'Avg R:R',      value: data?.avg_rr ? `${data.avg_rr.toFixed(2)}:1` : '—', cls: 'text-cyan' },
        ].map(({ label, value, cls }) => (
          <div key={label} className="rounded-xl border border-border p-4" style={{ background: '#131E30' }}>
            <p className="text-muted text-[11px] uppercase tracking-wider mb-1.5">{label}</p>
            <p className={`font-bold text-lg tabular-nums ${cls}`}>{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
