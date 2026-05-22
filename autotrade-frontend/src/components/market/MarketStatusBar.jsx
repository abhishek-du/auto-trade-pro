import { useEffect, useState } from 'react';
import { Wifi, WifiOff } from 'lucide-react';

function timeAgo(date) {
  if (!date) return '';
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs <  5)  return 'just now';
  if (secs < 60)  return `${secs}s ago`;
  return `${Math.floor(secs / 60)}m ago`;
}

function IndexChip({ data, label }) {
  if (!data) return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-border">
      <span className="text-muted text-xs">{label}</span>
      <span className="text-muted text-xs">—</span>
    </div>
  );
  const up = data.change_pct >= 0;
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-border">
      <span className="text-slate-300 text-xs font-semibold">{label}</span>
      <span className="text-slate-100 text-xs font-bold tabular-nums">
        {Number(data.price).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
      </span>
      <span className={`text-[11px] font-semibold tabular-nums ${up ? 'text-profit' : 'text-loss'}`}>
        {up ? '▲' : '▼'} {Math.abs(data.change_pct).toFixed(2)}%
      </span>
    </div>
  );
}

export default function MarketStatusBar({ summary, connected, lastUpdated }) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const status   = summary?.market_status ?? 'CLOSED';
  const istTime  = summary?.ist_time ?? '—';

  const statusColor = status === 'OPEN'
    ? 'text-profit'
    : status === 'PRE_OPEN'
    ? 'text-warn'
    : 'text-loss';

  const dotColor = status === 'OPEN'
    ? 'bg-profit'
    : status === 'PRE_OPEN'
    ? 'bg-warn'
    : 'bg-loss';

  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 rounded-xl border border-border text-sm"
      style={{ background: 'rgba(15,24,41,0.9)' }}
    >
      {/* Left — market status + IST clock */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${dotColor} ${status === 'OPEN' ? 'animate-pulse' : ''}`} />
          <span className={`font-bold text-xs uppercase tracking-wide ${statusColor}`}>
            NSE {status.replace('_', ' ')}
          </span>
        </div>
        <span className="text-muted text-xs tabular-nums">IST {istTime}</span>
      </div>

      {/* Center — index mini-chips */}
      <div className="flex items-center gap-2 flex-wrap">
        <IndexChip data={summary?.nifty50}    label="NIFTY"  />
        <IndexChip data={summary?.sensex}     label="SENSEX" />
        <IndexChip data={summary?.india_vix}  label="VIX"    />
      </div>

      {/* Right — WebSocket status + last updated */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          {connected ? (
            <>
              <span className="ws-live-dot" />
              <span className="text-profit text-xs font-medium">Live</span>
            </>
          ) : (
            <>
              <WifiOff size={12} className="text-muted" />
              <span className="text-muted text-xs">Polling</span>
            </>
          )}
        </div>
        {lastUpdated && (
          <span className="text-muted text-[11px] tabular-nums">
            Updated {timeAgo(lastUpdated)}
          </span>
        )}
      </div>
    </div>
  );
}
