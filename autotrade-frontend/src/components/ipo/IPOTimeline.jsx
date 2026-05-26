import { CheckCircle, Circle, Clock } from 'lucide-react'

const MILESTONES = [
  { key: 'open_date',       label: 'Opens'      },
  { key: 'close_date',      label: 'Closes'     },
  { key: 'allotment_date',  label: 'Allotment'  },
  { key: 'listing_date',    label: 'Listing'    },
]

function fmt(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: '2-digit' })
}

export default function IPOTimeline({ ipo, compact = false }) {
  const today = new Date()

  const dates = [
    ipo?.open_date_parsed   || ipo?.open_date,
    ipo?.close_date_parsed  || ipo?.close_date,
    ipo?.allotment_date,
    ipo?.listing_date,
  ]

  if (compact) {
    return (
      <div className="flex items-center gap-1 flex-wrap">
        {MILESTONES.map((m, i) => {
          const iso  = dates[i]
          const done = iso && new Date(iso) < today
          return (
            <div key={m.key} className="flex items-center gap-1 text-[10px]">
              <span className={done ? 'text-profit' : 'text-muted'}>{m.label}</span>
              <span className="text-muted/60">{iso ? fmt(iso) : 'TBA'}</span>
              {i < MILESTONES.length - 1 && <span className="text-border mx-0.5">›</span>}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div className="relative flex items-start gap-0">
      {MILESTONES.map((m, i) => {
        const iso  = dates[i]
        const done = iso && new Date(iso) < today
        const active = !done && iso && new Date(iso) >= today && (i === 0 || (dates[i - 1] && new Date(dates[i - 1]) < today))
        return (
          <div key={m.key} className="flex-1 flex flex-col items-center relative">
            {/* Connector line */}
            {i > 0 && (
              <div className={`absolute left-0 top-4 w-1/2 h-0.5 ${done ? 'bg-profit/50' : 'bg-border'}`} />
            )}
            {i < MILESTONES.length - 1 && (
              <div className={`absolute right-0 top-4 w-1/2 h-0.5 ${done ? 'bg-profit/50' : 'bg-border'}`} />
            )}
            {/* Dot */}
            <div className="relative z-10 mb-2">
              {done ? (
                <CheckCircle size={16} className="text-profit" />
              ) : active ? (
                <Clock size={16} className="text-cyan animate-pulse" />
              ) : (
                <Circle size={16} className="text-border" />
              )}
            </div>
            <p className={`text-[10px] font-semibold text-center ${done ? 'text-profit' : active ? 'text-cyan' : 'text-muted'}`}>{m.label}</p>
            <p className="text-[10px] text-muted/70 text-center mt-0.5">{iso ? fmt(iso) : 'TBA'}</p>
          </div>
        )
      })}
    </div>
  )
}
