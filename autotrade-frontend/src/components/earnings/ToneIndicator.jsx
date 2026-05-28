import { TrendingUp, TrendingDown, Minus, AlertCircle } from 'lucide-react'

const TONE_CONFIG = {
  OPTIMISTIC: { bg: 'bg-emerald-500/15', border: 'border-emerald-500/30', text: 'text-emerald-400', Icon: TrendingUp,   label: 'Optimistic' },
  CAUTIOUS:   { bg: 'bg-amber-500/15',   border: 'border-amber-500/30',   text: 'text-amber-400',   Icon: Minus,         label: 'Cautious'   },
  NEUTRAL:    { bg: 'bg-slate-500/15',   border: 'border-slate-500/30',   text: 'text-slate-400',   Icon: Minus,         label: 'Neutral'    },
  NEGATIVE:   { bg: 'bg-red-500/15',     border: 'border-red-500/30',     text: 'text-red-400',     Icon: TrendingDown,  label: 'Negative'   },
}

export default function ToneIndicator({ tone = 'NEUTRAL', reason = '', compact = false }) {
  const cfg = TONE_CONFIG[tone] || TONE_CONFIG.NEUTRAL
  const { bg, border, text, Icon, label } = cfg

  if (compact) {
    return (
      <span title={reason} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border ${bg} ${border} ${text}`}>
        <Icon size={10} />
        {label}
      </span>
    )
  }

  return (
    <div className={`rounded-xl border p-4 space-y-2 ${bg} ${border}`}>
      <div className="flex items-center gap-2">
        <div className={`p-2 rounded-lg ${bg} border ${border}`}>
          <Icon size={18} className={text} />
        </div>
        <div>
          <p className="text-muted text-[10px] uppercase tracking-widest font-semibold">Management Tone</p>
          <p className={`font-bold text-base ${text}`}>{label}</p>
        </div>
      </div>
      {reason && (
        <p className="text-slate-300 text-xs leading-relaxed italic">"{reason}"</p>
      )}
    </div>
  )
}
