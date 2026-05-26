function fmt(value, format) {
  if (typeof value === 'number') {
    if (format === 'count')  return value.toLocaleString('en-IN');
    if (format === 'plain')  return value.toFixed(2);
    const abs  = Math.abs(value);
    const sign = value < 0 ? '-' : '';
    if (abs >= 10_000_000) return sign + '₹' + (abs / 10_000_000).toFixed(2) + ' Cr';
    if (abs >= 100_000)    return sign + '₹' + (abs / 100_000).toFixed(2) + ' L';
    return sign + '₹' + abs.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return value;
}

export default function MetricCard({ title, value, subtitle, trend, icon: Icon, format }) {
  const up    = typeof trend === 'number' ? trend > 0 : null;
  const color = up === true ? 'text-profit' : up === false ? 'text-loss' : 'text-muted';
  const sign  = typeof trend === 'number' && trend > 0 ? '+' : '';

  return (
    <div className="relative overflow-hidden rounded-xl border border-border p-5 flex flex-col gap-3 transition-all duration-200 hover:border-accent/30 group"
      style={{ background: 'linear-gradient(135deg,#0F1829 0%,#131E30 100%)' }}>

      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none rounded-xl"
        style={{ background: 'radial-gradient(ellipse at top left,rgba(59,130,246,0.07),transparent 70%)' }} />

      <div className="flex items-center justify-between relative">
        <span className="text-muted text-[11px] font-semibold uppercase tracking-widest">{title}</span>
        {Icon && (
          <span className="p-2 rounded-lg border border-border" style={{ background: '#080D1A' }}>
            <Icon size={14} className="text-muted" />
          </span>
        )}
      </div>

      <div className="flex items-end justify-between gap-2 relative">
        <span className="text-slate-100 text-2xl font-bold leading-none tabular-nums">{fmt(value, format)}</span>
        {typeof trend === 'number' && (
          <span className={`text-sm font-bold ${color} tabular-nums`}>{sign}{trend.toFixed(2)}%</span>
        )}
      </div>

      {subtitle && <p className="text-muted text-xs leading-relaxed relative">{subtitle}</p>}
    </div>
  );
}
