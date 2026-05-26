import { useState } from 'react'
import { X, RefreshCw, ExternalLink } from 'lucide-react'
import IPOTimeline from './IPOTimeline'
import SubscriptionMeter from './SubscriptionMeter'
import GMPCard from './GMPCard'
import AnalysisPanel from './AnalysisPanel'

const STATUS_STYLES = {
  open:       { text: '#10B981', dot: 'bg-profit animate-pulse' },
  upcoming:   { text: '#3B82F6', dot: 'bg-accent' },
  announced:  { text: '#8B5CF6', dot: 'bg-purple-500' },
  listed:     { text: '#64748B', dot: 'bg-slate-500' },
  closed:     { text: '#64748B', dot: 'bg-slate-500' },
}

const TYPE_LABELS = { EQ: 'Mainboard', SME: 'SME', DEBT: 'Debt' }

export default function IPODetailPanel({ ipo, onClose, fetchAnalysis }) {
  const [analysis,  setAnalysis]  = useState(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [analysisError,   setAnalysisError]   = useState(null)

  async function loadAnalysis(forceRefresh = false) {
    setAnalysisLoading(true)
    setAnalysisError(null)
    try {
      const res = await fetchAnalysis(ipo.slug, forceRefresh)
      setAnalysis(res.analysis)
    } catch (err) {
      setAnalysisError(err.message)
    } finally {
      setAnalysisLoading(false)
    }
  }

  const st   = STATUS_STYLES[ipo.status] || STATUS_STYLES.listed
  const name = ipo.company_name || ipo.name || 'Unknown'

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between px-5 py-4 border-b border-border shrink-0">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-slate-100 font-bold text-base">{name}</h2>
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded border" style={{ color: st.text, borderColor: st.text + '40', background: st.text + '15' }}>
              <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle ${st.dot}`} />
              {ipo.status.toUpperCase()}
            </span>
            <span className="text-[10px] text-muted px-1.5 py-0.5 rounded border border-border">
              {TYPE_LABELS[ipo.ipo_type] || ipo.ipo_type}
            </span>
          </div>
          {ipo.sector && <p className="text-muted text-xs mt-0.5">{ipo.sector}</p>}
        </div>
        <button onClick={onClose} className="text-muted hover:text-white ml-4 shrink-0"><X size={16} /></button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">

        {/* Timeline */}
        <section>
          <p className="text-muted text-[10px] uppercase tracking-widest mb-3">Timeline</p>
          <IPOTimeline ipo={ipo} />
        </section>

        {/* Key details grid */}
        <section>
          <p className="text-muted text-[10px] uppercase tracking-widest mb-2">Issue Details</p>
          <div className="grid grid-cols-2 gap-2">
            {[
              ['Price Band',       ipo.price_display || 'TBA'],
              ['Issue Size',       ipo.issue_size_cr > 0 ? `₹${ipo.issue_size_cr.toFixed(0)} Cr` : 'TBA'],
              ['Lot Size',         ipo.lot_size || ipo.lotSize || 'TBA'],
              ['Min Investment',   ipo.min_investment ? `₹${Number(ipo.min_investment).toLocaleString('en-IN')}` : 'TBA'],
              ['Registrar',        ipo.registrar || 'TBA'],
              ['Listing Exchange', ipo.exchange || ipo.listing_at || 'NSE / BSE'],
            ].map(([label, val]) => (
              <div key={label} className="rounded-lg border border-border px-3 py-2" style={{ background: '#0a0f1c' }}>
                <p className="text-muted text-[10px]">{label}</p>
                <p className="text-slate-200 text-xs font-semibold mt-0.5 truncate">{val}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Promoter & Reservation */}
        {(ipo.promoter_holding_post || ipo.qib_portion || ipo.retail_portion) && (
          <section>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-2">Allocation</p>
            <div className="grid grid-cols-3 gap-2">
              {ipo.qib_portion    && <div className="rounded-lg border border-border px-2 py-2 text-center" style={{ background: '#0a0f1c' }}><p className="text-muted text-[9px]">QIB</p><p className="text-slate-200 text-xs font-bold">{ipo.qib_portion}</p></div>}
              {ipo.nii_portion    && <div className="rounded-lg border border-border px-2 py-2 text-center" style={{ background: '#0a0f1c' }}><p className="text-muted text-[9px]">NII</p><p className="text-slate-200 text-xs font-bold">{ipo.nii_portion}</p></div>}
              {ipo.retail_portion && <div className="rounded-lg border border-border px-2 py-2 text-center" style={{ background: '#0a0f1c' }}><p className="text-muted text-[9px]">Retail</p><p className="text-slate-200 text-xs font-bold">{ipo.retail_portion}</p></div>}
            </div>
          </section>
        )}

        {/* GMP */}
        <section>
          <p className="text-muted text-[10px] uppercase tracking-widest mb-2">Grey Market Premium</p>
          <GMPCard ipo={ipo} />
        </section>

        {/* Subscription */}
        <section>
          <p className="text-muted text-[10px] uppercase tracking-widest mb-2">Subscription</p>
          <SubscriptionMeter subscription={ipo.subscription} />
        </section>

        {/* About */}
        {ipo.about && (
          <section>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-2">About</p>
            <p className="text-slate-300 text-xs leading-relaxed">{ipo.about}</p>
          </section>
        )}

        {/* Links */}
        {(ipo.drhp_link || ipo.rhp_link || ipo.nse_url) && (
          <section>
            <p className="text-muted text-[10px] uppercase tracking-widest mb-2">Documents</p>
            <div className="flex flex-wrap gap-2">
              {ipo.drhp_link && <a href={ipo.drhp_link} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-accent hover:underline"><ExternalLink size={10} /> DRHP</a>}
              {ipo.rhp_link  && <a href={ipo.rhp_link}  target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-accent hover:underline"><ExternalLink size={10} /> RHP</a>}
              {ipo.nse_url   && <a href={ipo.nse_url}   target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-accent hover:underline"><ExternalLink size={10} /> NSE Page</a>}
            </div>
          </section>
        )}

        {/* AI Analysis */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <p className="text-muted text-[10px] uppercase tracking-widest">AI Analysis</p>
            {!analysis && !analysisLoading && (
              <button onClick={() => loadAnalysis(false)} className="text-[10px] text-accent hover:text-cyan transition-colors">
                Generate
              </button>
            )}
          </div>
          {analysisLoading ? (
            <div className="rounded-xl border border-border px-4 py-6 text-center" style={{ background: '#0a0f1c' }}>
              <RefreshCw size={16} className="animate-spin text-muted mx-auto mb-2" />
              <p className="text-muted text-xs">Analysing with Groq AI…</p>
            </div>
          ) : analysisError ? (
            <p className="text-red-400 text-xs">{analysisError}</p>
          ) : (
            <AnalysisPanel
              analysis={analysis}
              onRefresh={analysis ? () => loadAnalysis(true) : () => loadAnalysis(false)}
              loading={analysisLoading}
            />
          )}
        </section>
      </div>
    </div>
  )
}
