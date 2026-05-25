import { ChevronLeft, ChevronRight } from 'lucide-react'
import { getEventConfig } from '../../utils/eventTypeConfig'

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS   = ['January','February','March','April','May','June',
                  'July','August','September','October','November','December']

function EventPill({ event }) {
  const cfg = getEventConfig(event.event_type)
  return (
    <div
      className="text-[9px] font-semibold px-1 rounded truncate leading-4"
      style={{ background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}` }}
      title={event.title}
    >
      {event.title.length > 14 ? event.title.slice(0, 13) + '…' : event.title}
    </div>
  )
}

function DayCell({ day, dateStr, isToday, isOtherMonth, isSelected, events, onSelect }) {
  const uniqueTypes = [...new Set((events || []).map(e => e.event_type))]
  const hasHigh     = (events || []).some(e => e.importance === 'HIGH')
  const isHoliday   = (events || []).some(e => e.event_type === 'HOLIDAY')
  const isExpiry    = (events || []).some(e => e.event_type === 'FNO_EXPIRY')
  const isRBI       = (events || []).some(e => e.event_type === 'RBI_MPC' && e.title.includes('Decision'))
  const hasEvents   = (events || []).length > 0

  let cellBg = ''
  if (isSelected)    cellBg = 'bg-accent/30 border-accent/60'
  else if (isToday)  cellBg = 'border-cyan/50 bg-cyan/5'
  else if (isRBI)    cellBg = 'bg-red-900/10'
  else if (isExpiry) cellBg = 'bg-amber-900/8'
  else if (isHoliday) cellBg = 'bg-slate-800/40'

  return (
    <div
      onClick={() => hasEvents && onSelect(dateStr)}
      className={[
        'relative min-h-[90px] p-1.5 rounded-lg border transition-all',
        hasEvents ? 'cursor-pointer hover:border-accent/40 hover:bg-white/5' : '',
        isOtherMonth ? 'opacity-30' : '',
        isToday ? 'border-cyan/40' : 'border-border',
        cellBg,
      ].join(' ')}
    >
      {/* Date number */}
      <div className={[
        'text-xs font-bold leading-none mb-1',
        isToday ? 'text-cyan' : isOtherMonth ? 'text-muted/50' : 'text-slate-300',
      ].join(' ')}>
        {day}
        {isToday && (
          <span className="ml-1 text-[8px] font-semibold text-cyan/70">TODAY</span>
        )}
      </div>

      {/* Colored dots row */}
      {uniqueTypes.length > 0 && (
        <div className="flex items-center gap-0.5 mb-1 flex-wrap">
          {uniqueTypes.slice(0, 4).map(type => {
            const cfg = getEventConfig(type)
            return (
              <span
                key={type}
                title={cfg.label}
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ background: cfg.color }}
              />
            )
          })}
          {uniqueTypes.length > 4 && (
            <span className="text-[8px] text-muted">+{uniqueTypes.length - 4}</span>
          )}
        </div>
      )}

      {/* Event pills — desktop only */}
      <div className="hidden sm:flex flex-col gap-0.5">
        {(events || []).slice(0, 2).map((ev, i) => (
          <EventPill key={i} event={ev} />
        ))}
        {(events || []).length > 2 && (
          <div className="text-[9px] text-muted pl-1">+{events.length - 2} more</div>
        )}
      </div>
    </div>
  )
}

export default function CalendarGrid({
  year, month,
  eventsByDate,
  selectedDate,
  onDateSelect,
}) {
  const today      = new Date()
  const todayStr   = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`
  const firstDay   = new Date(year, month, 1).getDay()  // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const daysInPrev  = new Date(year, month, 0).getDate()

  // Build 6×7 grid cells
  const cells = []

  // Leading cells from previous month
  for (let i = firstDay - 1; i >= 0; i--) {
    const d = daysInPrev - i
    const prev = new Date(year, month - 1)
    const ds = `${prev.getFullYear()}-${String(prev.getMonth()+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`
    cells.push({ day: d, dateStr: ds, isOtherMonth: true })
  }

  // Current month cells
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`
    cells.push({ day: d, dateStr: ds, isOtherMonth: false })
  }

  // Trailing cells from next month
  const remaining = 42 - cells.length
  for (let d = 1; d <= remaining; d++) {
    const next = new Date(year, month + 1)
    const ds = `${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`
    cells.push({ day: d, dateStr: ds, isOtherMonth: true })
  }

  return (
    <div>
      {/* Weekday headers */}
      <div className="grid grid-cols-7 gap-1 mb-1">
        {WEEKDAYS.map(d => (
          <div key={d} className="text-center text-[10px] font-semibold text-muted uppercase tracking-wider py-1">
            {d}
          </div>
        ))}
      </div>

      {/* Date grid */}
      <div className="grid grid-cols-7 gap-1">
        {cells.map((cell, i) => (
          <DayCell
            key={i}
            day={cell.day}
            dateStr={cell.dateStr}
            isToday={cell.dateStr === todayStr}
            isOtherMonth={cell.isOtherMonth}
            isSelected={cell.dateStr === selectedDate}
            events={eventsByDate[cell.dateStr]}
            onSelect={onDateSelect}
          />
        ))}
      </div>
    </div>
  )
}
