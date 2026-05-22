import { useEffect, useRef, useState } from 'react';

function fmt(n, dec = 2) {
  if (n == null || n === 0) return '—';
  return Number(n).toLocaleString('en-IN', {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

function RangeBar({ low, high, current }) {
  if (!low || !high || !current || high <= low) return null;
  const pct = Math.min(100, Math.max(0, ((current - low) / (high - low)) * 100));
  return (
    <div className="space-y-1">
      <div className="relative h-1.5 rounded-full bg-white/10">
        <div
          className="absolute top-0 left-0 h-full rounded-full bg-gradient-to-r from-loss via-warn to-profit"
          style={{ width: '100%' }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full bg-white border-2 border-slate-900 shadow"
          style={{ left: `calc(${pct}% - 5px)` }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-muted tabular-nums">
        <span>{fmt(low, 0)}</span>
        <span className="text-slate-400">52w range</span>
        <span>{fmt(high, 0)}</span>
      </div>
    </div>
  );
}

export default function IndexCard({ data, small }) {
  const prevPriceRef = useRef(null);
  const [flashClass, setFlashClass] = useState('');

  useEffect(() => {
    if (!data?.price) return;
    const prev = prevPriceRef.current;
    if (prev !== null && prev !== data.price) {
      const cls = data.price > prev ? 'flash-green' : 'flash-red';
      setFlashClass(cls);
      const timer = setTimeout(() => setFlashClass(''), 800);
      prevPriceRef.current = data.price;
      return () => clearTimeout(timer);
    }
    prevPriceRef.current = data.price;
  }, [data?.price]);

  if (!data) {
    return (
      <div className="bg-panel border border-border rounded-xl p-4 animate-pulse h-36" />
    );
  }

  const up      = data.change_pct >= 0;
  const pctAbs  = Math.abs(data.change_pct ?? 0);
  const chgAbs  = Math.abs(data.change ?? 0);

  return (
    <div className={`bg-panel border border-border rounded-xl p-4 space-y-3 transition-colors ${flashClass}`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-slate-100 font-bold text-sm">{data.name}</p>
          <p className="text-muted text-[10px] uppercase tracking-wide mt-0.5">
            {data.type === 'index' ? 'NSE Index' : data.symbol}
          </p>
        </div>
        <span className={`text-xs font-bold px-2 py-0.5 rounded ${up ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>
          {up ? '▲' : '▼'} {pctAbs.toFixed(2)}%
        </span>
      </div>

      {/* Price hero */}
      <div>
        <p className={`tabular-nums font-extrabold ${small ? 'text-xl' : 'text-3xl'} ${up ? 'text-profit' : 'text-loss'}`}>
          {Number(data.price).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
        </p>
        <p className={`text-xs tabular-nums mt-0.5 ${up ? 'text-profit/80' : 'text-loss/80'}`}>
          {up ? '+' : '−'}{fmt(chgAbs)} today
        </p>
      </div>

      {/* OHLC mini row */}
      <div className="grid grid-cols-3 gap-1 text-[11px]">
        {[
          { label: 'Open',  val: data.open },
          { label: 'High',  val: data.high },
          { label: 'Low',   val: data.low  },
        ].map(({ label, val }) => (
          <div key={label} className="bg-surface/60 rounded px-2 py-1">
            <p className="text-muted text-[10px]">{label}</p>
            <p className="text-slate-300 tabular-nums font-medium">
              {Number(val || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </p>
          </div>
        ))}
      </div>

      {/* 52-week range bar */}
      {!small && (
        <RangeBar low={data['52w_low']} high={data['52w_high']} current={data.price} />
      )}
    </div>
  );
}
