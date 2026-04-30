function formatValue(value) {
  if (typeof value === 'number') {
    if (Math.abs(value) >= 1000) {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
    }
    return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return value;
}

export default function MetricCard({ title, value, subtitle, trend, icon: Icon }) {
  const trendPositive = typeof trend === 'number' ? trend > 0 : null;
  const trendColor =
    trendPositive === true  ? 'text-profit' :
    trendPositive === false ? 'text-loss'   : 'text-neutral';

  const trendSign = typeof trend === 'number' && trend > 0 ? '+' : '';

  return (
    <div className="bg-panel border border-border rounded-xl p-5 flex flex-col gap-3 hover:border-accent/50 transition-colors">
      <div className="flex items-center justify-between">
        <span className="text-muted text-xs font-medium uppercase tracking-wider">{title}</span>
        {Icon && (
          <span className="p-2 bg-surface rounded-lg text-muted">
            <Icon size={16} />
          </span>
        )}
      </div>

      <div className="flex items-end justify-between gap-2">
        <span className="text-slate-100 text-2xl font-bold leading-none tabular-nums">
          {formatValue(value)}
        </span>
        {typeof trend === 'number' && (
          <span className={`text-sm font-semibold ${trendColor} tabular-nums`}>
            {trendSign}{trend.toFixed(2)}%
          </span>
        )}
      </div>

      {subtitle && (
        <p className="text-muted text-xs leading-relaxed">{subtitle}</p>
      )}
    </div>
  );
}
