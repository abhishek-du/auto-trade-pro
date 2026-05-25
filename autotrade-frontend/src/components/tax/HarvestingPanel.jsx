import { useState } from 'react'
import { TrendingDown, TrendingUp, Clock, Zap, ChevronDown, ChevronUp } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

const TABS = [
  { id: 'loss',   label: 'Loss Harvesting',  icon: TrendingDown },
  { id: 'gain',   label: 'Gain Harvesting',  icon: TrendingUp   },
  { id: 'timing', label: 'Timing Tips',      icon: Clock        },
]

function OpportunityCard({ opp, type }) {
  const [expanded, setExpanded] = useState(false)

  if (type === 'loss') return (
    <div className="rounded-xl border border-red-500/20 p-4 space-y-3" style={{ background: 'rgba(239,68,68,0.04)' }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-slate-100 font-semibold text-sm">{opp.symbol}</p>
          <p className="text-muted text-xs truncate max-w-[200px]">{opp.company_name}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <p className="text-loss font-bold text-base tabular-nums">{formatINR(opp.unrealized_loss, 0)}</p>
          <p className="text-loss text-xs">{opp.unrealized_loss_pct.toFixed(1)}%</p>
        </div>
      </div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 text-[10px] font-bold">{opp.loss_type}</span>
          <span className="text-muted text-[10px]">{opp.holding_days}d held</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Zap size={11} className="text-profit" />
          <span className="text-profit font-semibold text-xs">Saves {formatINR(opp.estimated_tax_saved, 0)}</span>
        </div>
      </div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-muted text-[10px] hover:text-slate-400 transition-colors"
      >
        {expanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
        Details
      </button>
      {expanded && (
        <div className="rounded-lg bg-surface/50 p-3 space-y-1.5 text-xs">
          <p className="text-muted">{opp.action}</p>
          <p className="text-muted/60 italic">{opp.note}</p>
        </div>
      )}
    </div>
  )

  if (type === 'gain') return (
    <div className="rounded-xl border border-profit/20 p-4 space-y-3" style={{ background: 'rgba(34,197,94,0.04)' }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-slate-100 font-semibold text-sm">{opp.symbol}</p>
          <p className="text-muted text-xs truncate max-w-[200px]">{opp.company_name}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <p className="text-profit font-bold text-base tabular-nums">{formatINR(opp.bookable_gain, 0)}</p>
          <p className="text-muted text-xs">tax-free</p>
        </div>
      </div>
      <div className="flex items-center justify-between">
        <span className="px-2 py-0.5 rounded-full bg-profit/15 text-profit text-[10px] font-bold">LTCG Exempt</span>
        <span className="text-muted text-xs">Sell {opp.bookable_units} units</span>
      </div>
      <p className="text-muted text-[10px]">{opp.note}</p>
    </div>
  )

  if (type === 'timing') return (
    <div className="rounded-xl border border-blue-500/20 p-4 space-y-3" style={{ background: 'rgba(59,130,246,0.04)' }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-slate-100 font-semibold text-sm">{opp.symbol}</p>
          <p className="text-muted text-xs">{opp.company_name}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <p className="text-blue-400 font-bold text-xl tabular-nums">{opp.days_to_ltcg}d</p>
          <p className="text-muted text-[10px]">to LTCG</p>
        </div>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex items-center justify-between text-[10px] text-muted mb-1">
          <span>{opp.holding_days} days held</span>
          <span>365 days</span>
        </div>
        <div className="h-2 bg-surface rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-blue-500 transition-all"
            style={{ width: `${(opp.holding_days / 365) * 100}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg bg-surface/60 p-2">
          <p className="text-muted text-[9px]">Sell now (STCG)</p>
          <p className="text-loss font-semibold">{formatINR(opp.stcg_tax_if_sold_now, 0)}</p>
        </div>
        <div className="rounded-lg bg-surface/60 p-2">
          <p className="text-muted text-[9px]">Wait {opp.days_to_ltcg}d (LTCG)</p>
          <p className="text-profit font-semibold">{formatINR(opp.ltcg_tax_after_waiting, 0)}</p>
        </div>
      </div>

      <div className="flex items-center gap-1.5">
        <Zap size={11} className="text-profit" />
        <span className="text-profit font-semibold text-xs">Potential saving: {formatINR(opp.potential_saving, 0)}</span>
      </div>
    </div>
  )
}

export default function HarvestingPanel({ harvesting, loading }) {
  const [tab, setTab] = useState('loss')

  if (loading) return (
    <div className="flex items-center justify-center h-40 text-muted text-sm">Loading opportunities…</div>
  )

  if (!harvesting) return (
    <div className="flex items-center justify-center h-40 text-muted text-sm">No data available</div>
  )

  const { loss_harvest = [], gain_harvest = [], timing_suggestions = [], summary = {} } = harvesting

  const tabData = tab === 'loss'   ? loss_harvest :
                  tab === 'gain'   ? gain_harvest :
                                     timing_suggestions

  return (
    <div className="space-y-4">
      {/* Summary strip */}
      {summary.total_tax_saveable > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-profit/30 px-4 py-3 bg-profit/5">
          <Zap size={14} className="text-profit flex-shrink-0" />
          <div>
            <span className="text-profit font-bold text-sm">Save up to {formatINR(summary.total_tax_saveable, 0)}</span>
            <span className="text-muted text-xs ml-2">in tax this financial year</span>
          </div>
          <div className="ml-auto text-right flex-shrink-0">
            <p className="text-muted text-[10px]">LTCG exemption left</p>
            <p className="text-accent font-semibold text-sm">{formatINR(summary.ltcg_exemption_remaining, 0)}</p>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-surface rounded-xl p-1 w-fit">
        {TABS.map(t => {
          const Icon  = t.icon
          const count = t.id === 'loss' ? loss_harvest.length :
                        t.id === 'gain' ? gain_harvest.length :
                        timing_suggestions.length
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${
                tab === t.id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'
              }`}
            >
              <Icon size={11} />
              {t.label}
              {count > 0 && (
                <span className={`px-1.5 py-0.5 rounded-full text-[9px] font-bold ${
                  tab === t.id ? 'bg-accent/30 text-accent' : 'bg-surface text-muted'
                }`}>{count}</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Tab content */}
      {tab === 'loss' && (
        <>
          {loss_harvest.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 gap-2">
              <TrendingUp size={24} className="text-profit/60" />
              <p className="text-muted text-sm">No loss harvesting opportunities</p>
              <p className="text-muted/50 text-xs">Your portfolio is fully in profit 🎉</p>
            </div>
          ) : (
            <>
              <p className="text-muted text-xs">
                Book these losses to offset ₹{((harvesting.existing_stcg || 0)/1000).toFixed(0)}K in gains.
              </p>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {loss_harvest.map((o, i) => <OpportunityCard key={i} opp={o} type="loss" />)}
              </div>
            </>
          )}
        </>
      )}

      {tab === 'gain' && (
        <>
          {summary.ltcg_exemption_remaining > 0 && (
            <div className="rounded-lg border border-profit/20 bg-profit/5 px-4 py-2.5 flex items-center gap-2">
              <TrendingUp size={13} className="text-profit" />
              <p className="text-xs text-profit font-medium">
                {formatINR(summary.ltcg_exemption_remaining, 0)} of ₹1.25L LTCG exemption still unused this year
              </p>
            </div>
          )}

          {gain_harvest.length === 0 ? (
            summary.ltcg_exemption_remaining <= 0 ? (
              <div className="flex flex-col items-center justify-center h-32 gap-2">
                <p className="text-muted text-sm">₹1.25L LTCG exemption fully used this year</p>
                <p className="text-muted/50 text-xs">Come back in April to harvest gains again</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-32 gap-2">
                <p className="text-muted text-sm">No long-term holdings with bookable gains</p>
                <p className="text-muted/50 text-xs">Stocks need 12+ months holding for LTCG</p>
              </div>
            )
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {gain_harvest.map((o, i) => <OpportunityCard key={i} opp={o} type="gain" />)}
              </div>
              <div className="rounded-lg border border-border bg-accent/5 px-4 py-3">
                <p className="text-xs text-muted">
                  <span className="text-accent font-semibold">Year-end tip:</span> Book up to ₹1.25L LTCG every March before year-end,
                  then immediately rebuy the same stocks. This resets your cost basis tax-free and compounds your effective returns.
                </p>
              </div>
            </>
          )}
        </>
      )}

      {tab === 'timing' && (
        <>
          {timing_suggestions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 gap-2">
              <Clock size={24} className="text-muted/40" />
              <p className="text-muted text-sm">No timing suggestions</p>
              <p className="text-muted/50 text-xs">No holdings are close to the 12-month LTCG threshold</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              {timing_suggestions.map((o, i) => <OpportunityCard key={i} opp={o} type="timing" />)}
            </div>
          )}
        </>
      )}
    </div>
  )
}
