import { ChevronRight, Clock } from 'lucide-react'
import GMPCard from './GMPCard'
import SubscriptionMeter from './SubscriptionMeter'

const STATUS_STYLES = {
  open:       { bg: 'rgba(16,185,129,0.12)',  border: 'rgba(16,185,129,0.4)',  text: '#10B981',  dot: 'bg-profit animate-pulse' },
  upcoming:   { bg: 'rgba(59,130,246,0.10)',  border: 'rgba(59,130,246,0.3)',  text: '#3B82F6',  dot: 'bg-accent' },
  announced:  { bg: 'rgba(139,92,246,0.10)',  border: 'rgba(139,92,246,0.3)',  text: '#8B5CF6',  dot: 'bg-purple-500' },
  listed:     { bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.25)',text: '#64748B',  dot: 'bg-slate-500' },
  closed:     { bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.25)',text: '#64748B',  dot: 'bg-slate-500' },
}

const TYPE_LABELS = { EQ: 'Mainboard', SME: 'SME', DEBT: 'Debt' }

export default function IPOCard({ ipo, onClick }) {
  const st    = STATUS_STYLES[ipo.status] || STATUS_STYLES.listed
  const name  = ipo.company_name || ipo.name || 'Unknown'
  const daysToClose = ipo.days_to_close

  return (
    <div
      onClick={() => onClick?.(ipo)}
      className="rounded-xl border cursor-pointer transition-all hover:border-accent/40 hover:shadow-lg active:scale-[0.99] select-none"
      style={{ background: '#0F1829', borderColor: 'rgba(51,65,85,0.6)' }}
    >
      <div className="p-4 space-y-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-slate-100 font-semibold text-sm leading-tight truncate">{name}</p>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded border" style={{ color: st.text, background: st.bg, borderColor: st.border }}>
                <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle ${st.dot}`} />
                {ipo.status.toUpperCase()}
              </span>
              <span className="text-[10px] text-muted px-1.5 py-0.5 rounded border border-border">
                {TYPE_LABELS[ipo.ipo_type] || ipo.ipo_type}
              </span>
              {ipo.sector && (
                <span className="text-[10px] text-muted/70 truncate max-w-[120px]">{ipo.sector}</span>
              )}
            </div>
          </div>
          <ChevronRight size={14} className="text-muted shrink-0 mt-1" />
        </div>

        {/* Key metrics row */}
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <p className="text-muted text-[10px]">Price Band</p>
            <p className="text-slate-200 text-xs font-semibold">{ipo.price_display || 'TBA'}</p>
          </div>
          <div>
            <p className="text-muted text-[10px]">Issue Size</p>
            <p className="text-slate-200 text-xs font-semibold">
              {ipo.issue_size_cr > 0 ? `₹${ipo.issue_size_cr.toFixed(0)} Cr` : 'TBA'}
            </p>
          </div>
          <div>
            <p className="text-muted text-[10px]">Lot Size</p>
            <p className="text-slate-200 text-xs font-semibold">{ipo.lot_size || ipo.lotSize || 'TBA'}</p>
          </div>
        </div>

        {/* GMP compact */}
        <GMPCard ipo={ipo} compact={true} />

        {/* Subscription compact */}
        {ipo.subscription && <SubscriptionMeter subscription={ipo.subscription} compact={true} />}

        {/* Timeline / days to close */}
        {ipo.status === 'open' && daysToClose != null && (
          <div className="flex items-center gap-1.5 text-[10px] text-amber-400">
            <Clock size={11} />
            {daysToClose === 0 ? 'Closes today' : `Closes in ${daysToClose} day${daysToClose !== 1 ? 's' : ''}`}
          </div>
        )}
        {ipo.status === 'upcoming' && ipo.open_date_parsed && (
          <p className="text-[10px] text-muted">Opens {new Date(ipo.open_date_parsed).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}</p>
        )}
        {ipo.status === 'listed' && ipo.listing_date && (
          <p className="text-[10px] text-muted">Listed {new Date(ipo.listing_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: '2-digit' })}</p>
        )}
      </div>
    </div>
  )
}
