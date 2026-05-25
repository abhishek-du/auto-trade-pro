import { ASSET_CLASSES } from '../../hooks/useAllocation'

const PROFILES = [
  { id: 'conservative',         label: 'Conservative',         color: '#10B981', cagr: '7-9%',   horizon: '< 3 yrs',   alloc: { large_cap: 20, debt: 60, gold: 15, cash: 5 } },
  { id: 'moderate_conservative',label: 'Moderate-Cons.',       color: '#06B6D4', cagr: '9-11%',  horizon: '3-5 yrs',   alloc: { large_cap: 30, mid_cap: 5, debt: 45, gold: 15, cash: 5 } },
  { id: 'moderate',             label: 'Moderate',              color: '#3B82F6', cagr: '11-13%', horizon: '5-7 yrs',   alloc: { large_cap: 40, mid_cap: 15, small_cap: 5, debt: 30, gold: 10 } },
  { id: 'moderate_aggressive',  label: 'Mod. Aggressive',      color: '#F59E0B', cagr: '12-15%', horizon: '7-10 yrs',  alloc: { large_cap: 45, mid_cap: 20, small_cap: 10, debt: 20, gold: 5 } },
  { id: 'aggressive',           label: 'Aggressive',            color: '#F97316', cagr: '14-18%', horizon: '10+ yrs',   alloc: { large_cap: 35, mid_cap: 30, small_cap: 20, debt: 10, gold: 5 } },
  { id: 'very_aggressive',      label: 'Very Aggressive',       color: '#EF4444', cagr: '16-22%', horizon: '15+ yrs',   alloc: { large_cap: 25, mid_cap: 35, small_cap: 35, debt: 5 } },
]

const ALLOC_ORDER = ['large_cap', 'mid_cap', 'small_cap', 'international', 'gold', 'debt', 'cash']

export default function RiskProfileSelector({ currentProfile, onSelect, recommendedProfile }) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin">
      {PROFILES.map(p => {
        const isActive = currentProfile === p.id
        const isRec    = recommendedProfile === p.id

        return (
          <button
            key={p.id}
            onClick={() => onSelect(p.id)}
            className={`flex-shrink-0 w-36 rounded-xl border p-3 text-left transition-all ${
              isActive
                ? 'border-accent/60 text-slate-100'
                : 'border-border text-muted hover:border-border/80 hover:text-slate-300'
            }`}
            style={isActive ? { background: `${p.color}18` } : { background: '#0a0f1c' }}
          >
            {isRec && (
              <div className="text-[9px] font-bold mb-1.5 flex items-center gap-1" style={{ color: p.color }}>
                ★ Recommended
              </div>
            )}

            <div className="font-semibold text-xs truncate mb-1"
              style={{ color: isActive ? p.color : undefined }}>
              {p.label}
            </div>

            {/* Mini allocation bar */}
            <div className="flex h-2 rounded-full overflow-hidden gap-px mb-2">
              {ALLOC_ORDER.map(cls => {
                const pct = p.alloc[cls] || 0
                if (pct === 0) return null
                return (
                  <div
                    key={cls}
                    style={{ width: `${pct}%`, background: ASSET_CLASSES[cls]?.color }}
                    title={`${ASSET_CLASSES[cls]?.label} ${pct}%`}
                  />
                )
              })}
            </div>

            <div className="text-[10px] text-muted">{p.cagr}</div>
            <div className="text-[9px] text-muted/60">{p.horizon}</div>
          </button>
        )
      })}
    </div>
  )
}
