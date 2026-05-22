import { useState } from 'react';
import { TrendingUp, TrendingDown, Activity } from 'lucide-react';

const TABS = [
  { key: 'top_gainers', label: 'Top Gainers',  Icon: TrendingUp,   color: 'text-profit' },
  { key: 'top_losers',  label: 'Top Losers',   Icon: TrendingDown, color: 'text-loss'   },
  { key: 'most_active', label: 'Most Active',  Icon: Activity,     color: 'text-cyan'   },
];

function fmtVol(n) {
  if (!n) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function MoverRow({ item, tab }) {
  const up  = (item.change_pct ?? 0) >= 0;
  const pct = item.change_pct ?? 0;

  return (
    <div className="flex items-center justify-between px-3 py-2.5 rounded-lg hover:bg-surface/60 transition-colors">
      <div className="min-w-0">
        <p className="text-slate-100 font-bold text-[13px] leading-tight truncate">
          {(item.symbol ?? '').replace('.NS', '')}
        </p>
        <p className="text-muted text-[11px] truncate max-w-[120px]">{item.name}</p>
      </div>

      <div className="flex items-center gap-3 shrink-0 ml-3">
        <span className="text-slate-300 tabular-nums text-sm font-medium">
          {Number(item.price ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
        </span>
        {tab === 'most_active' ? (
          <span className="text-cyan text-xs tabular-nums font-medium">
            {fmtVol(item.volume)}
          </span>
        ) : (
          <span className={`text-xs font-semibold px-2 py-0.5 rounded tabular-nums ${up ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
            {up ? '▲' : '▼'} {Math.abs(pct).toFixed(2)}%
          </span>
        )}
      </div>
    </div>
  );
}

export default function TopMoversPanel({ topMovers }) {
  const [active, setActive] = useState('top_gainers');

  const items = topMovers?.[active] ?? [];

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      {/* Tab bar */}
      <div className="flex border-b border-border">
        {TABS.map(({ key, label, Icon, color }) => (
          <button
            key={key}
            onClick={() => setActive(key)}
            className={[
              'flex items-center gap-1.5 flex-1 justify-center px-3 py-3 text-xs font-semibold transition-colors',
              active === key
                ? `${color} border-b-2 border-current bg-white/[0.03]`
                : 'text-muted hover:text-slate-300',
            ].join(' ')}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {/* Items */}
      <div className="p-2">
        {items.length === 0 ? (
          <p className="text-muted text-xs text-center py-6">No data yet — waiting for cache</p>
        ) : (
          items.map((item) => <MoverRow key={item.symbol} item={item} tab={active} />)
        )}
      </div>
    </div>
  );
}
