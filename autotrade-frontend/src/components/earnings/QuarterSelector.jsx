export default function QuarterSelector({ quarters = [], cachedQuarters = new Set(), selected, onChange, loading }) {
  if (!quarters.length) return null
  return (
    <div className="flex items-center gap-2 overflow-x-auto pb-1">
      <span className="text-muted text-xs whitespace-nowrap shrink-0">Quarter:</span>
      {quarters.filter(Boolean).map(q => {
        const isSelected = q === selected
        const isCached   = cachedQuarters.has(q)
        return (
          <button
            key={q}
            onClick={() => onChange(q)}
            disabled={loading}
            className={`relative flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all whitespace-nowrap border ${
              isSelected
                ? 'bg-accent/20 border-accent/40 text-cyan'
                : 'border-border text-muted hover:text-slate-200 hover:bg-white/5'
            }`}
          >
            {loading && isSelected && (
              <div className="w-2.5 h-2.5 border border-current border-t-transparent rounded-full animate-spin" />
            )}
            {q}
            {isCached && !isSelected && (
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" title="Summary cached" />
            )}
          </button>
        )
      })}
    </div>
  )
}
