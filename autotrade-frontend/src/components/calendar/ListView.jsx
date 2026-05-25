import { getEventConfig } from '../../utils/eventTypeConfig'
import { Clock, CheckCircle, AlertCircle } from 'lucide-react'

const MON_NAMES = ['January','February','March','April','May','June',
                   'July','August','September','October','November','December']
const DAY_NAMES = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']

function formatDate(dateStr) {
  const d = new Date(dateStr + 'T00:00:00')
  return `${DAY_NAMES[d.getDay()]}, ${d.getDate()} ${MON_NAMES[d.getMonth()]}`
}

export default function ListView({ eventsByDate }) {
  const sortedDates = Object.keys(eventsByDate).sort()

  if (sortedDates.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-muted text-sm gap-2">
        <p>No events match the selected filters</p>
      </div>
    )
  }

  const today = new Date()
  today.setHours(0,0,0,0)

  return (
    <div className="space-y-4">
      {sortedDates.map(dateStr => {
        const evDate = new Date(dateStr + 'T00:00:00')
        const isPast = evDate < today
        const isToday = evDate.getTime() === today.getTime()
        const events = eventsByDate[dateStr]

        return (
          <div key={dateStr}>
            {/* Date header */}
            <div className={[
              'flex items-center gap-3 mb-2',
              isPast ? 'opacity-50' : '',
            ].join(' ')}>
              <div className={[
                'text-xs font-bold px-2.5 py-1 rounded-lg',
                isToday ? 'bg-cyan/20 text-cyan' : 'bg-panel border border-border text-slate-300',
              ].join(' ')}>
                {formatDate(dateStr)}
                {isToday && <span className="ml-1.5 text-[9px] text-cyan/70">TODAY</span>}
              </div>
              <div className="flex-1 h-px bg-border" />
              <span className="text-muted text-[10px]">{events.length} event{events.length !== 1 ? 's' : ''}</span>
            </div>

            {/* Event cards */}
            <div className="space-y-2 pl-2">
              {events.map((ev, i) => {
                const cfg = getEventConfig(ev.event_type)
                return (
                  <div
                    key={i}
                    className="flex items-start gap-3 p-3 rounded-xl border"
                    style={{ borderColor: cfg.border, background: cfg.bg }}
                  >
                    <div className="w-1 self-stretch rounded-full shrink-0" style={{ background: cfg.color }} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                        <span
                          className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider"
                          style={{ color: cfg.color }}
                        >
                          {cfg.label}
                        </span>
                        {ev.importance === 'HIGH' && (
                          <span className="text-[9px] font-semibold text-red-400">HIGH IMPACT</span>
                        )}
                        {ev.is_confirmed
                          ? <span className="flex items-center gap-0.5 text-[9px] text-profit"><CheckCircle size={9}/> Confirmed</span>
                          : <span className="flex items-center gap-0.5 text-[9px] text-warn"><AlertCircle size={9}/> Tentative</span>
                        }
                      </div>
                      <p className="text-slate-100 font-semibold text-sm">{ev.title}</p>
                      <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                        {ev.time_ist && (
                          <span className="flex items-center gap-1 text-muted text-xs">
                            <Clock size={9} /> {ev.time_ist} IST
                          </span>
                        )}
                        {ev.company_name && (
                          <span className="text-muted text-xs">{ev.company_name}</span>
                        )}
                        {ev.symbol && (
                          <span className="text-cyan/70 text-xs font-mono">{ev.symbol}</span>
                        )}
                      </div>
                      {ev.description && (
                        <p className="text-muted/70 text-[11px] mt-1 leading-relaxed">{ev.description}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
