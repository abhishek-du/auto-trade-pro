import { ASSET_CLASSES } from '../../hooks/useAllocation'

export default function DeviationMeter({ assetClass, current, target, threshold = 5 }) {
  const dev     = current - target
  const danger  = Math.abs(dev) >= threshold
  const warning = Math.abs(dev) >= threshold * 0.5
  const color   = danger ? '#EF4444' : warning ? '#F59E0B' : '#22C55E'

  const range   = Math.max(threshold * 2.5, Math.abs(dev) * 1.4)
  const pct     = ((dev + range) / (range * 2)) * 100
  const cfg     = ASSET_CLASSES[assetClass] || {}

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted">{cfg.label || assetClass}</span>
        <span style={{ color }} className="font-semibold">
          {dev > 0 ? '+' : ''}{dev.toFixed(1)}%
          {Math.abs(dev) < threshold ? ' ✓' : (dev > 0 ? ' overweight' : ' underweight')}
        </span>
      </div>
      <div className="relative h-2 rounded-full overflow-hidden bg-surface">
        {/* Center target line */}
        <div className="absolute top-0 bottom-0 w-px bg-slate-500/60" style={{ left: '50%' }} />
        {/* Fill bar */}
        <div
          className="absolute top-0 bottom-0 rounded-full transition-all duration-500"
          style={{
            left:  dev >= 0 ? '50%' : `${pct}%`,
            width: `${Math.abs(dev) / (range * 2) * 100}%`,
            background: color,
            opacity: 0.8,
          }}
        />
      </div>
      <div className="flex justify-between text-[9px] text-muted/50">
        <span>−{range.toFixed(0)}%</span>
        <span>Target {target}%</span>
        <span>+{range.toFixed(0)}%</span>
      </div>
    </div>
  )
}
