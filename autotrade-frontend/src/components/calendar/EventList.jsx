import { X, CheckCircle, AlertCircle, Clock, Building2 } from 'lucide-react'
import { getEventConfig } from '../../utils/eventTypeConfig'

const DAY_NAMES  = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
const MON_NAMES  = ['January','February','March','April','May','June',
                    'July','August','September','October','November','December']

function formatEventDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00')
  return `${DAY_NAMES[d.getDay()]}, ${d.getDate()} ${MON_NAMES[d.getMonth()]} ${d.getFullYear()}`
}

function ExtraInfo({ event }) {
  const meta = event.metadata || {}

  if (event.event_type === 'RBI_MPC') {
    return (
      <div className="mt-2 px-3 py-2 rounded-lg bg-red-950/30 border border-red-900/30 text-xs space-y-0.5">
        <p className="text-slate-300">Repo Rate: <span className="font-bold text-white">{meta.current_rate}%</span></p>
        <p className="text-muted">Governor: {meta.governor}</p>
      </div>
    )
  }

  if (event.event_type === 'FNO_EXPIRY') {
    const indices = meta.indices || []
    return (
      <div className="mt-2 px-3 py-2 rounded-lg bg-amber-950/20 border border-amber-900/30 text-xs space-y-0.5">
        <p className="text-muted">Exchange: <span className="text-slate-300">{meta.exchange || 'NSE'}</span></p>
        {indices.length > 0 && (
          <p className="text-muted">Indices: <span className="text-slate-300">{indices.join(', ')}</span></p>
        )}
        {meta.adjusted && (
          <p className="text-amber-400/80">Date adjusted from holiday (original: {meta.original_date})</p>
        )}
      </div>
    )
  }

  if (event.event_type === 'IPO') {
    return (
      <div className="mt-2 px-3 py-2 rounded-lg bg-violet-950/20 border border-violet-900/30 text-xs space-y-0.5">
        {meta.issue_price_range && <p className="text-muted">Price Band: <span className="text-slate-300">{meta.issue_price_range}</span></p>}
        {meta.lot_size          && <p className="text-muted">Lot Size: <span className="text-slate-300">{meta.lot_size}</span></p>}
        {meta.issue_size_cr     && <p className="text-muted">Issue Size: <span className="text-slate-300">₹{meta.issue_size_cr} Cr</span></p>}
      </div>
    )
  }

  if (event.event_type === 'EARNINGS') {
    return (
      <div className="mt-2 px-3 py-2 rounded-lg bg-teal-950/20 border border-teal-900/30 text-xs space-y-0.5">
        {meta.est_eps && <p className="text-muted">Est. EPS: <span className="text-slate-300">₹{meta.est_eps}</span></p>}
        {meta.sector  && <p className="text-muted">Sector: <span className="text-slate-300">{meta.sector}</span></p>}
      </div>
    )
  }

  return null
}

function EventCard({ event }) {
  const cfg = getEventConfig(event.event_type)
  return (
    <div
      className="rounded-xl border overflow-hidden"
      style={{ borderColor: cfg.border, background: cfg.bg }}
    >
      <div className="flex items-start gap-3 p-3">
        {/* Colored left strip */}
        <div className="w-1 self-stretch rounded-full shrink-0" style={{ background: cfg.color }} />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span
              className="text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider"
              style={{ background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}` }}
            >
              {cfg.label}
            </span>
            {event.is_confirmed
              ? <span className="flex items-center gap-0.5 text-[9px] text-profit"><CheckCircle size={10} /> Confirmed</span>
              : <span className="flex items-center gap-0.5 text-[9px] text-warn"><AlertCircle size={10} /> Tentative</span>
            }
          </div>

          <p className="text-slate-100 font-semibold text-sm leading-snug">{event.title}</p>

          {event.time_ist && (
            <p className="flex items-center gap-1 text-muted text-xs mt-0.5">
              <Clock size={10} /> {event.time_ist} IST
            </p>
          )}

          {(event.company_name || event.symbol) && (
            <p className="flex items-center gap-1 text-muted text-xs mt-0.5">
              <Building2 size={10} />
              {event.company_name || ''}{event.symbol ? ` (${event.symbol})` : ''}
            </p>
          )}

          {event.description && (
            <p className="text-muted/80 text-[11px] mt-1.5 leading-relaxed line-clamp-2">
              {event.description}
            </p>
          )}

          <ExtraInfo event={event} />
        </div>
      </div>
    </div>
  )
}

export default function EventList({ events, date, onClose }) {
  return (
    <div className="bg-panel border border-border rounded-xl overflow-hidden flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div>
          <p className="text-slate-100 font-semibold text-sm">{formatEventDate(date)}</p>
          <p className="text-muted text-xs">{events.length} event{events.length !== 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-white/10 transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {/* Events */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-24 text-muted text-sm">
            No events for this date
          </div>
        ) : (
          events.map((ev, i) => <EventCard key={i} event={ev} />)
        )}
      </div>
    </div>
  )
}
