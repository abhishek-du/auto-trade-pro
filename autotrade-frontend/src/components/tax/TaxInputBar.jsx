import { useState } from 'react'
import { Info } from 'lucide-react'

const INCOME_PRESETS = [
  { label: '₹5L',   value: 500000 },
  { label: '₹10L',  value: 1000000 },
  { label: '₹25L',  value: 2500000 },
  { label: '₹50L',  value: 5000000 },
  { label: '₹1Cr',  value: 10000000 },
]

function getSlabLabel(income) {
  if (income <= 400000)   return '0%'
  if (income <= 800000)   return '5%'
  if (income <= 1200000)  return '10%'
  if (income <= 1600000)  return '15%'
  if (income <= 2000000)  return '20%'
  if (income <= 2400000)  return '25%'
  return '30%'
}

function Tooltip({ text }) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative inline-block">
      <button
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        className="text-muted hover:text-slate-400"
      >
        <Info size={11} />
      </button>
      {show && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 rounded-lg border border-border glass-panel p-2 text-[10px] text-muted shadow-xl">
          {text}
        </div>
      )}
    </div>
  )
}

export default function TaxInputBar({
  financialYear, setFinancialYear, availableFYs,
  annualIncome,  setAnnualIncome,
  alreadyUsedLTCG, setAlreadyUsedLTCG,
}) {
  const allFYs = [...new Set(['FY2024-25', 'FY2025-26', 'FY2026-27', ...(availableFYs || [])])].sort().reverse()
  const slab   = getSlabLabel(annualIncome)

  return (
    <div className="flex flex-wrap items-end gap-4 rounded-xl border border-border px-5 py-4 glass-panel">
      {/* FY selector */}
      <div className="space-y-1">
        <label className="text-muted text-[10px] uppercase tracking-widest">Financial Year</label>
        <select
          value={financialYear}
          onChange={e => setFinancialYear(e.target.value)}
          className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-accent"
        >
          {allFYs.map(fy => (
            <option key={fy} value={fy}>{fy} {fy === 'FY2025-26' ? '(Current)' : ''}</option>
          ))}
        </select>
      </div>

      {/* Annual Income */}
      <div className="space-y-1">
        <div className="flex items-center gap-1.5">
          <label className="text-muted text-[10px] uppercase tracking-widest">Annual Income (₹)</label>
          <Tooltip text="Your total annual income helps calculate surcharge. Capital gains rates are flat, but surcharge kicks in above ₹50L." />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">₹</span>
            <input
              type="number"
              min="0"
              step="100000"
              value={annualIncome}
              onChange={e => setAnnualIncome(+e.target.value)}
              className="bg-surface border border-border rounded-lg pl-6 pr-3 py-2 text-sm text-slate-200 w-36 focus:outline-none focus:border-accent"
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {INCOME_PRESETS.map(p => (
              <button
                key={p.value}
                onClick={() => setAnnualIncome(p.value)}
                className={`px-2 py-1.5 rounded text-[10px] font-semibold transition-colors ${
                  annualIncome === p.value
                    ? 'bg-accent/20 text-accent border border-accent/30'
                    : 'bg-surface text-muted border border-border hover:text-slate-300'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Other LTCG used */}
      <div className="space-y-1">
        <div className="flex items-center gap-1.5">
          <label className="text-muted text-[10px] uppercase tracking-widest">Other LTCG Used (₹)</label>
          <Tooltip text="LTCG booked from other sources (stocks, MF) reduces your ₹1.25L annual exemption." />
        </div>
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">₹</span>
          <input
            type="number"
            min="0"
            step="1000"
            value={alreadyUsedLTCG}
            onChange={e => setAlreadyUsedLTCG(+e.target.value)}
            className="bg-surface border border-border rounded-lg pl-6 pr-3 py-2 text-sm text-slate-200 w-32 focus:outline-none focus:border-accent"
          />
        </div>
      </div>

      {/* Slab badge */}
      <div className="space-y-1">
        <label className="text-muted text-[10px] uppercase tracking-widest">Your Slab Rate</label>
        <div className={`px-4 py-2 rounded-lg border text-sm font-bold tabular-nums ${
          slab === '0%'  ? 'bg-profit/10 border-profit/30 text-profit' :
          slab === '30%' ? 'bg-red-500/10 border-red-500/30 text-red-400' :
                           'bg-amber-500/10 border-amber-500/30 text-amber-400'
        }`}>
          {slab}
        </div>
      </div>
    </div>
  )
}
