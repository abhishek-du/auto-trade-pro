import { getEventConfig } from '../../utils/eventTypeConfig'

const FILTER_TYPES = [
  'IPO', 'EARNINGS', 'RBI_MPC', 'FNO_EXPIRY', 'HOLIDAY', 'FII_DII_RELEASE',
]

export default function FilterBar({ activeFilters, onToggle, typeCounts = {} }) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {FILTER_TYPES.map(type => {
        const cfg    = getEventConfig(type)
        const active = activeFilters[type] !== false
        const count  = typeCounts[type] || 0

        return (
          <button
            key={type}
            onClick={() => onToggle(type)}
            className={[
              'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border transition-all select-none',
              active
                ? 'text-white'
                : 'text-muted bg-panel hover:text-slate-300',
            ].join(' ')}
            style={
              active
                ? { background: cfg.bg, borderColor: cfg.border, color: cfg.color }
                : { borderColor: 'rgba(51,65,85,0.6)' }
            }
          >
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: active ? cfg.color : '#475569' }}
            />
            {cfg.label}
            {count > 0 && (
              <span
                className="text-[9px] px-1 rounded-full font-bold"
                style={active ? { background: cfg.color, color: '#fff' } : { background: '#334155', color: '#94a3b8' }}
              >
                {count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
