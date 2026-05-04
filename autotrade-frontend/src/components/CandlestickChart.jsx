import { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { getPortfolioSnapshots } from '../api/client';

const fmtUSD = (n) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(n ?? 0);

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border px-3 py-2 text-xs shadow-xl" style={{ background: '#131E30' }}>
      <p className="text-muted mb-1">{label}</p>
      <p className="text-slate-100 font-bold">{fmtUSD(payload[0]?.value)}</p>
    </div>
  );
}

export default function CandlestickChart() {
  const [data, setData]       = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getPortfolioSnapshots()
      .then((rows) => {
        const pts = (Array.isArray(rows) ? rows : []).map((r) => ({
          date:   r.date?.slice(5) ?? '',
          equity: r.equity ?? r.balance ?? 0,
        }));
        setData(pts.length ? pts : [{ date: 'Start', equity: 1000 }]);
      })
      .catch(() => setData([{ date: 'Start', equity: 1000 }]))
      .finally(() => setLoading(false));
  }, []);

  const vals  = data.map((d) => d.equity);
  const min   = Math.min(...vals) * 0.997;
  const max   = Math.max(...vals) * 1.003;
  const last  = vals[vals.length - 1] ?? 1000;
  const first = vals[0] ?? 1000;
  const up    = last >= first;
  const color = up ? '#10B981' : '#F43F5E';

  return (
    <div className="rounded-xl border border-border overflow-hidden h-80 flex flex-col" style={{ background: '#0F1829' }}>
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
        <div>
          <h2 className="text-slate-100 font-semibold text-sm">Equity Curve</h2>
          <p className="text-muted text-xs mt-0.5">Simulated portfolio balance</p>
        </div>
        <div className="text-right">
          <p className="text-slate-100 font-bold tabular-nums">{fmtUSD(last)}</p>
          <p className={`text-xs font-semibold tabular-nums ${up ? 'text-profit' : 'text-loss'}`}>
            {up ? '+' : ''}{(((last - first) / (first || 1)) * 100).toFixed(2)}%
          </p>
        </div>
      </div>

      <div className="flex-1 px-2 py-3">
        {loading ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-muted text-sm">Loading chart…</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={color} stopOpacity={0.2} />
                  <stop offset="95%" stopColor={color} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1E2D45" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#4E6280', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis domain={[min, max]} tick={{ fill: '#4E6280', fontSize: 10 }} axisLine={false} tickLine={false}
                tickFormatter={(v) => `$${v.toFixed(0)}`} width={52} />
              <Tooltip content={<ChartTooltip />} />
              <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={2}
                fill="url(#equityGrad)" dot={false} activeDot={{ r: 4, fill: color }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
