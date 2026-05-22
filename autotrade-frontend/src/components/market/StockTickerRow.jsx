import { useEffect, useRef, useState } from 'react';

function fmtVol(n) {
  if (!n) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function RangePosition({ low, high, current }) {
  if (!low || !high || high <= low) return <span className="text-muted text-xs">—</span>;
  const pct = Math.min(100, Math.max(0, ((current - low) / (high - low)) * 100));
  return (
    <div className="flex items-center gap-1.5 min-w-[80px]">
      <div className="relative flex-1 h-1 rounded-full bg-white/10">
        <div
          className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-slate-400"
          style={{ left: `calc(${pct}% - 4px)` }}
        />
      </div>
      <span className="text-muted text-[10px] tabular-nums">{pct.toFixed(0)}%</span>
    </div>
  );
}

export default function StockTickerRow({ symbol, name, price, change, change_pct, volume, type, w52_low, w52_high }) {
  const prevRef    = useRef(null);
  const [flash, setFlash] = useState('');

  useEffect(() => {
    if (price == null) return;
    const prev = prevRef.current;
    if (prev !== null && prev !== price) {
      const cls = price > prev ? 'flash-green' : 'flash-red';
      setFlash(cls);
      const t = setTimeout(() => setFlash(''), 800);
      prevRef.current = price;
      return () => clearTimeout(t);
    }
    prevRef.current = price;
  }, [price]);

  const up = (change_pct ?? 0) >= 0;

  return (
    <tr className={`border-b border-border/40 hover:bg-surface/60 transition-colors cursor-pointer ${flash}`}>
      {/* Stock name */}
      <td className="px-4 py-2.5">
        <div className="font-bold text-slate-100 text-[13px] leading-tight">{symbol?.replace('.NS', '')}</div>
        <div className="text-muted text-[11px] mt-0.5 truncate max-w-[100px]">{name}</div>
      </td>

      {/* LTP */}
      <td className="px-4 py-2.5 text-right">
        <span className={`tabular-nums font-semibold text-sm ${up ? 'text-profit' : 'text-loss'}`}>
          {Number(price ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
        </span>
      </td>

      {/* Change */}
      <td className="px-4 py-2.5 text-right">
        <span className={`tabular-nums text-xs font-medium ${up ? 'text-profit' : 'text-loss'}`}>
          {up ? '+' : ''}{Number(change ?? 0).toFixed(2)}
        </span>
      </td>

      {/* Change % */}
      <td className="px-4 py-2.5 text-right">
        <span className={`inline-block tabular-nums text-xs font-semibold px-1.5 py-0.5 rounded ${up ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
          {up ? '▲' : '▼'} {Math.abs(change_pct ?? 0).toFixed(2)}%
        </span>
      </td>

      {/* Volume */}
      <td className="px-4 py-2.5 text-right text-muted text-xs tabular-nums">
        {fmtVol(volume)}
      </td>

      {/* 52W Position */}
      <td className="px-4 py-2.5">
        <RangePosition low={w52_low} high={w52_high} current={price} />
      </td>
    </tr>
  );
}
