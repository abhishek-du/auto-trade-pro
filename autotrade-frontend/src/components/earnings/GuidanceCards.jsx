const CARDS = [
  { key: 'revenue_guidance', label: 'Revenue',  emoji: '📈', color: 'text-emerald-400', bg: 'bg-emerald-500/8', border: 'border-emerald-500/20' },
  { key: 'margin_guidance',  label: 'Margins',  emoji: '⚖️', color: 'text-blue-400',    bg: 'bg-blue-500/8',    border: 'border-blue-500/20'    },
  { key: 'capex_guidance',   label: 'Capex',    emoji: '🏗️', color: 'text-amber-400',   bg: 'bg-amber-500/8',   border: 'border-amber-500/20'   },
  { key: 'dividend_info',    label: 'Dividend', emoji: '💰', color: 'text-cyan',         bg: 'bg-cyan/8',        border: 'border-cyan/20'        },
]

export default function GuidanceCards({ revenue_guidance, margin_guidance, capex_guidance, dividend_info }) {
  const vals = { revenue_guidance, margin_guidance, capex_guidance, dividend_info }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {CARDS.map(({ key, label, emoji, color, bg, border }) => {
        const val = vals[key]
        const hasVal = val && val !== 'null' && !val.toLowerCase().includes('not mentioned')
        return (
          <div key={key} className={`rounded-xl border p-3 space-y-1.5 ${bg} ${border}`} style={{ background: undefined }}>
            <div className="flex items-center gap-1.5">
              <span className="text-sm">{emoji}</span>
              <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">{label}</p>
            </div>
            {hasVal ? (
              <p className={`text-xs font-semibold leading-snug ${color}`}>{val}</p>
            ) : (
              <p className="text-muted/50 text-xs italic">Not disclosed</p>
            )}
          </div>
        )
      })}
    </div>
  )
}
