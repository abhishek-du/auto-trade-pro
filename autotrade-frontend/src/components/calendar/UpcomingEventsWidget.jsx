import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getEventConfig, daysAwayLabel } from '../../utils/eventTypeConfig'
import { apiFetch } from '../../api/client'

const MON_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const DAY_ABBR = ['SUN','MON','TUE','WED','THU','FRI','SAT']

function DateBadge({ dateStr }) {
  const d = new Date(dateStr + 'T00:00:00')
  return (
    <div className="flex flex-col items-center justify-center w-10 shrink-0 text-center">
      <span className="text-[9px] font-bold text-muted">{DAY_ABBR[d.getDay()]}</span>
      <span className="text-base font-extrabold text-slate-100 leading-none">{d.getDate()}</span>
      <span className="text-[9px] font-semibold text-cyan/70">{MON_ABBR[d.getMonth()]}</span>
    </div>
  )
}

export default function UpcomingEventsWidget({ events: propEvents, compact = false, maxItems = 8 }) {
  const navigate = useNavigate()
  const [fetchedEvents, setFetchedEvents] = useState([])

  // Self-fetch when not passed events from parent
  useEffect(() => {
    if (propEvents !== undefined) return
    apiFetch('/api/v1/india/calendar/upcoming?days=14')
      .then(d => setFetchedEvents(d.events || []))
      .catch(() => {})
  }, [propEvents])

  const events = propEvents !== undefined ? propEvents : fetchedEvents
  const today = new Date()
  today.setHours(0,0,0,0)

  const displayEvents = events.slice(0, maxItems)

  if (displayEvents.length === 0) {
    return (
      <div className="bg-panel border border-border rounded-xl p-4">
        <p className="text-slate-200 font-semibold text-sm mb-2">Upcoming Events</p>
        <p className="text-muted text-xs text-center py-4">No upcoming events in the next 14 days</p>
      </div>
    )
  }

  // Group by date
  const grouped = {}
  displayEvents.forEach(ev => {
    grouped[ev.event_date] = grouped[ev.event_date] || []
    grouped[ev.event_date].push(ev)
  })

  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <p className="text-slate-200 font-semibold text-sm">Upcoming Events</p>
        <button
          onClick={() => navigate('/calendar')}
          className="text-cyan/70 text-xs hover:text-cyan transition-colors"
        >
          View all →
        </button>
      </div>

      <div className="divide-y divide-border">
        {Object.entries(grouped).map(([dateStr, dayEvents]) => {
          const evDate = new Date(dateStr + 'T00:00:00')
          const daysAway = Math.round((evDate - today) / 86400000)

          return (
            <div key={dateStr} className="px-3 py-2">
              {/* Date separator */}
              <div className="flex items-center gap-2 mb-1.5">
                <div className="flex-1 h-px bg-border/60" />
                {(() => { const { label, cls } = daysAwayLabel(daysAway); return <span className={`text-[10px] font-semibold shrink-0 ${cls}`}>{label}</span> })()}
                <div className="flex-1 h-px bg-border/60" />
              </div>

              {dayEvents.map((ev, i) => {
                const cfg = getEventConfig(ev.event_type)
                return (
                  <div
                    key={i}
                    onClick={() => navigate(`/calendar?date=${dateStr}`)}
                    className="flex items-center gap-2.5 py-1.5 px-1 rounded-lg hover:bg-white/5 cursor-pointer transition-colors"
                  >
                    {!compact && <DateBadge dateStr={dateStr} />}

                    <div
                      className="w-0.5 self-stretch rounded-full shrink-0"
                      style={{ background: cfg.color }}
                    />

                    <div className="flex-1 min-w-0">
                      <span
                        className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider"
                        style={{ color: cfg.color, background: cfg.bg }}
                      >
                        {cfg.label}
                      </span>
                      <p className="text-slate-300 text-xs font-medium leading-tight mt-0.5 truncate">
                        {ev.title.length > 32 ? ev.title.slice(0, 31) + '…' : ev.title}
                      </p>
                      {ev.symbol && (
                        <p className="text-muted text-[10px]">{ev.symbol}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
