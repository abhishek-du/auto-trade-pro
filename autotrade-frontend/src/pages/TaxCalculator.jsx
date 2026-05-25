import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Receipt, ChevronDown, ChevronUp, BarChart2,
  List, Leaf, Calculator, ExternalLink,
} from 'lucide-react'
import { useTaxCalculator } from '../hooks/useTaxCalculator'
import { usePortfolioTracker } from '../hooks/usePortfolioTracker'
import TaxInputBar           from '../components/tax/TaxInputBar'
import TaxSummaryCards       from '../components/tax/TaxSummaryCards'
import TaxWaterfall          from '../components/tax/TaxWaterfall'
import TradeBreakdownTable   from '../components/tax/TradeBreakdownTable'
import HarvestingPanel       from '../components/tax/HarvestingPanel'
import StandaloneCalculator  from '../components/tax/StandaloneCalculator'
import LoadingSpinner        from '../components/LoadingSpinner'

const TABS = [
  { id: 'summary',    label: 'Summary',         icon: BarChart2  },
  { id: 'breakdown',  label: 'Trade Breakdown',  icon: List       },
  { id: 'harvesting', label: 'Tax Harvesting',   icon: Leaf       },
  { id: 'calculator', label: 'Calculator',       icon: Calculator },
]

const TAX_RULES = [
  { asset: 'Equity / Equity MF',       holding: '≤12 months',  rate: '20% STCG',          notes: 'Section 111A' },
  { asset: 'Equity / Equity MF',       holding: '>12 months',  rate: '12.5% LTCG',        notes: '₹1.25L exempt p.a. (Sec 112A)' },
  { asset: 'Debt MF (post Apr-2023)',  holding: 'Any',          rate: 'Slab rate',          notes: 'Section 50AA — no LTCG benefit' },
  { asset: 'Debt MF (pre Apr-2023)',   holding: '≤24 months',  rate: 'Slab rate',          notes: 'STCG' },
  { asset: 'Debt MF (pre Apr-2023)',   holding: '>24 months',  rate: '12.5% LTCG',        notes: 'No indexation (post Jul-23, 2024)' },
]

export default function TaxCalculator() {
  const [searchParams] = useSearchParams()
  const urlPortfolioId = searchParams.get('portfolio')

  // Get active portfolio from the tracker hook
  const { portfolios, activeId } = usePortfolioTracker()
  const portfolioId = urlPortfolioId || activeId

  const {
    financialYear, setFinancialYear, availableFYs,
    annualIncome,  setAnnualIncome,
    alreadyUsedLTCG, setAlreadyUsedLTCG,
    taxSummary, breakdown, harvesting,
    loading, error,
  } = useTaxCalculator(portfolioId)

  const [tab,         setTab]         = useState('summary')
  const [showRules,   setShowRules]   = useState(false)

  const activePortfolio = portfolios.find(p => p.id === portfolioId)

  return (
    <div className="space-y-5 fade-in">
      {/* ── Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-slate-100 text-xl font-bold flex items-center gap-2">
            <Receipt size={18} className="text-cyan" />
            Tax P&amp;L Calculator
          </h1>
          <p className="text-muted text-sm mt-0.5">
            Indian capital gains — {financialYear}
            {activePortfolio && <span className="ml-2 text-muted/60">· {activePortfolio.name}</span>}
          </p>
        </div>
      </div>

      {/* ── Tax input bar ── */}
      <TaxInputBar
        financialYear={financialYear} setFinancialYear={setFinancialYear} availableFYs={availableFYs}
        annualIncome={annualIncome}   setAnnualIncome={setAnnualIncome}
        alreadyUsedLTCG={alreadyUsedLTCG} setAlreadyUsedLTCG={setAlreadyUsedLTCG}
      />

      {/* ── No portfolio notice ── */}
      {!portfolioId && (
        <div className="rounded-xl border border-border/40 px-5 py-4 flex items-center gap-3" style={{ background: '#0a0f1c' }}>
          <ExternalLink size={14} className="text-muted flex-shrink-0" />
          <p className="text-muted text-sm">
            Connect a portfolio in <a href="/portfolio-tracker" className="text-accent hover:underline">My Holdings</a> to
            see tax analysis on your actual trades. The Calculator tab works without a portfolio.
          </p>
        </div>
      )}

      {/* ── Tax rules info box ── */}
      <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0F1829' }}>
        <button
          onClick={() => setShowRules(!showRules)}
          className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-white/2 transition-colors"
        >
          <div className="flex items-center gap-2 text-sm text-slate-300 font-medium">
            <Receipt size={14} className="text-cyan" />
            Current Tax Rules (Budget 2024 — effective Jul 23, 2024)
          </div>
          {showRules ? <ChevronUp size={14} className="text-muted" /> : <ChevronDown size={14} className="text-muted" />}
        </button>

        {showRules && (
          <div className="px-5 pb-4 space-y-3 border-t border-border">
            <div className="overflow-x-auto">
              <table className="w-full text-xs mt-3">
                <thead>
                  <tr className="text-muted text-[9px] uppercase tracking-wider border-b border-border">
                    <th className="text-left pb-2">Asset Type</th>
                    <th className="text-left pb-2 px-3">Holding</th>
                    <th className="text-left pb-2 px-3">Rate</th>
                    <th className="text-left pb-2">Notes</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {TAX_RULES.map((r, i) => (
                    <tr key={i} className="text-slate-300">
                      <td className="py-2 pr-3">{r.asset}</td>
                      <td className="py-2 px-3 text-muted">{r.holding}</td>
                      <td className="py-2 px-3 font-semibold text-amber-400">{r.rate}</td>
                      <td className="py-2 text-muted">{r.notes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-muted">
              + 4% Health &amp; Education Cess on all tax amounts.
              Surcharge applies for income &gt;₹50L (capped at 15% for equity gains under Sec 111A/112A).
            </p>
          </div>
        )}
      </div>

      {/* ── Tabs ── */}
      <div className="flex items-center gap-0.5 bg-panel border border-border rounded-xl p-1 w-fit">
        {TABS.map(t => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-colors ${
                tab === t.id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-slate-300'
              }`}
            >
              <Icon size={12} /> {t.label}
            </button>
          )
        })}
      </div>

      {/* ── Tab content ── */}
      {loading && tab !== 'calculator' ? (
        <LoadingSpinner message="Computing tax…" />
      ) : error && tab !== 'calculator' ? (
        <div className="rounded-xl border border-red-500/20 px-5 py-4 text-red-400 text-sm" style={{ background: 'rgba(239,68,68,0.04)' }}>
          {error}
        </div>
      ) : (
        <>
          {tab === 'summary' && portfolioId && (
            <div className="space-y-5">
              <TaxSummaryCards taxSummary={taxSummary} />
              <TaxWaterfall    taxSummary={taxSummary} />
            </div>
          )}

          {tab === 'summary' && !portfolioId && (
            <div className="rounded-xl border border-border/40 flex flex-col items-center justify-center h-48 gap-3" style={{ background: '#0a0f1c' }}>
              <Receipt size={28} className="text-muted/30" />
              <p className="text-muted text-sm">No portfolio connected</p>
              <p className="text-muted/50 text-xs">Use the Calculator tab for manual entries</p>
            </div>
          )}

          {tab === 'breakdown' && portfolioId && (
            <div className="rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
              <TradeBreakdownTable breakdown={breakdown} />
            </div>
          )}

          {tab === 'breakdown' && !portfolioId && (
            <div className="rounded-xl border border-border/40 flex flex-col items-center justify-center h-48 gap-2" style={{ background: '#0a0f1c' }}>
              <p className="text-muted text-sm">Connect a portfolio to see trade breakdown</p>
            </div>
          )}

          {tab === 'harvesting' && (
            <div className="rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
              {portfolioId ? (
                <HarvestingPanel harvesting={harvesting} loading={loading} />
              ) : (
                <div className="flex flex-col items-center justify-center h-48 gap-2">
                  <p className="text-muted text-sm">Connect a portfolio to see harvesting opportunities</p>
                </div>
              )}
            </div>
          )}

          {tab === 'calculator' && (
            <div className="rounded-xl border border-border p-5" style={{ background: '#0F1829' }}>
              <StandaloneCalculator />
            </div>
          )}
        </>
      )}

      {/* ── Disclaimer ── */}
      <div className="rounded-xl border border-border/30 px-5 py-4" style={{ background: '#0a0f1c' }}>
        <p className="text-muted/60 text-[10px] text-center leading-relaxed">
          Disclaimer: This calculator provides estimates only. Tax liability depends on your total income,
          applicable surcharge, deductions, and other factors. Consult a qualified CA or tax advisor before
          filing your ITR. Tax rules may change — verify with latest CBDT guidance.
          Results do not account for STT, brokerage, or transaction costs.
        </p>
      </div>
    </div>
  )
}
