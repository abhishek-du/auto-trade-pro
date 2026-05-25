import { ASSET_CLASSES } from '../../hooks/useAllocation'

const ORDER = ['large_cap', 'mid_cap', 'small_cap', 'international', 'gold', 'debt', 'cash', 'other']

function StackedBar({ data, label, threshold }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between mb-1">
        <p className="text-muted text-[10px] uppercase tracking-widest">{label}</p>
      </div>
      <div className="flex h-7 rounded-lg overflow-hidden gap-px w-full">
        {ORDER.map(cls => {
          const pct   = data[cls] ?? 0
          const cfg   = ASSET_CLASSES[cls]
          if (pct < 0.5) return null
          return (
            <div
              key={cls}
              style={{ width: `${pct}%`, background: cfg.color, flexShrink: 0 }}
              title={`${cfg.label}: ${pct.toFixed(1)}%`}
              className="relative group flex items-center justify-center overflow-hidden"
            >
              {pct > 8 && (
                <span className="text-[9px] font-bold text-white/80 truncate px-0.5">
                  {Math.round(pct)}%
                </span>
              )}
            </div>
          )
        })}
      </div>
      {/* Segment labels */}
      <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-1">
        {ORDER.map(cls => {
          const pct = data[cls] ?? 0
          if (pct < 1) return null
          return (
            <span key={cls} className="text-[9px] text-muted" style={{ color: ASSET_CLASSES[cls]?.color }}>
              {ASSET_CLASSES[cls]?.label} {pct.toFixed(0)}%
            </span>
          )
        })}
      </div>
    </div>
  )
}

export default function AllocationBars({ current, target, showDeviation = true, threshold = 5 }) {
  const currentPcts = Object.fromEntries(
    Object.entries(current || {}).map(([k, v]) => [k, v.total_pct || 0])
  )

  if (!Object.keys(currentPcts).length) return null

  return (
    <div className="space-y-4">
      <StackedBar data={currentPcts} label="Current Allocation" threshold={threshold} />
      {target && Object.keys(target).length > 0 && (
        <StackedBar data={target} label="Target Allocation" threshold={threshold} />
      )}

      {showDeviation && target && (
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest mb-2">Deviation from Target</p>
          <div className="flex flex-wrap gap-2">
            {ORDER.map(cls => {
              const cur = currentPcts[cls] ?? 0
              const tgt = target[cls]     ?? 0
              if (cur === 0 && tgt === 0) return null
              const dev = cur - tgt
              if (Math.abs(dev) < 0.5) return null
              const over = dev > 0
              const danger = Math.abs(dev) >= threshold
              return (
                <div
                  key={cls}
                  className="flex items-center gap-1 px-2 py-1 rounded-full text-[10px] font-semibold"
                  style={{
                    background: danger
                      ? (over ? 'rgba(239,68,68,0.12)' : 'rgba(34,197,94,0.1)')
                      : 'rgba(100,116,139,0.1)',
                    border: `1px solid ${danger ? (over ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.3)') : 'rgba(100,116,139,0.2)'}`,
                    color: danger ? (over ? '#EF4444' : '#22C55E') : '#64748B',
                  }}
                >
                  {over ? '▲' : '▼'} {over ? '+' : ''}{dev.toFixed(1)}% {ASSET_CLASSES[cls]?.label}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
