import { formatINR } from '../../utils/indianFormat'

function WaterfallRow({ label, amount, running, isSubtotal, isTotal, positive, negative, faint }) {
  const isPos    = amount > 0
  const isNeg    = amount < 0
  const abs      = Math.abs(amount)
  const barColor = isTotal    ? '#ef4444' :
                   isSubtotal ? '#f97316' :
                   positive   ? '#22c55e' :
                   negative   ? '#ef4444' :
                   faint      ? '#64748b' : '#64748b'

  const rowBg = isTotal    ? 'bg-red-500/5 border-red-500/20' :
                isSubtotal ? 'bg-amber-500/5 border-amber-500/20' : 'border-transparent'

  return (
    <div className={`flex items-center gap-3 rounded-lg border px-3 py-2 ${rowBg}`}>
      <div className="w-52 flex-shrink-0">
        <p className={`text-xs ${isTotal || isSubtotal ? 'font-semibold text-slate-200' : 'text-muted'}`}>
          {label}
        </p>
      </div>
      <div className="flex-1 h-3 bg-surface rounded-full overflow-hidden">
        {abs > 0 && (
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${Math.min(100, abs / 1)}%`, background: barColor }}
          />
        )}
      </div>
      <div className="w-28 text-right flex-shrink-0">
        <span className={`text-xs font-semibold tabular-nums ${
          isPos && !faint ? 'text-profit' :
          isNeg           ? 'text-loss'   :
          isTotal         ? 'text-red-400':
          isSubtotal      ? 'text-amber-400' : 'text-muted'
        }`}>
          {amount > 0 ? '+' : ''}{formatINR(amount, 0)}
        </span>
      </div>
    </div>
  )
}

export default function TaxWaterfall({ taxSummary }) {
  if (!taxSummary) return null

  const {
    stcg_equity_gains, stcg_equity_losses, stcg_equity_net,
    stcg_tax_before_cess, stcg_cess, stcg_total_tax,
    ltcg_equity_gains, ltcg_equity_losses, ltcg_equity_net,
    ltcg_exempt_used, ltcg_taxable,
    ltcg_tax_before_cess, ltcg_cess, ltcg_total_tax,
    debt_slab_gains, debt_slab_net, debt_slab_tax, slab_rate,
    total_tax,
  } = taxSummary

  const hasSTCG  = stcg_equity_gains > 0 || stcg_equity_losses > 0
  const hasLTCG  = ltcg_equity_gains > 0 || ltcg_equity_losses > 0
  const hasDebt  = debt_slab_gains > 0

  if (!hasSTCG && !hasLTCG && !hasDebt) return null

  return (
    <div className="space-y-2">
      <p className="text-muted text-[10px] uppercase tracking-widest pt-1">Step-by-Step Tax Calculation</p>

      <div className="rounded-xl border border-border overflow-hidden" style={{ background: '#0a0f1c' }}>
        <div className="px-4 py-3 space-y-1.5">

          {hasSTCG && (
            <>
              <p className="text-amber-400 text-[10px] font-semibold uppercase tracking-wider pt-1">Short-Term Capital Gains</p>
              <WaterfallRow label="Gross STCG"          amount={stcg_equity_gains}  positive />
              {stcg_equity_losses > 0 &&
                <WaterfallRow label="Less: STCL set-off"   amount={-stcg_equity_losses} negative />}
              <WaterfallRow label="Net STCG"            amount={Math.max(0, stcg_equity_net)} isSubtotal />
              {stcg_equity_net > 0 && <>
                <WaterfallRow label="STCG Tax @ 20%"      amount={stcg_tax_before_cess} faint />
                <WaterfallRow label="+ Cess 4%"           amount={stcg_cess}            faint />
                <WaterfallRow label="STCG Total Tax"      amount={stcg_total_tax}       isSubtotal />
              </>}
            </>
          )}

          {hasLTCG && (
            <>
              <p className="text-blue-400 text-[10px] font-semibold uppercase tracking-wider pt-2">Long-Term Capital Gains</p>
              <WaterfallRow label="Gross LTCG"           amount={ltcg_equity_gains}  positive />
              {ltcg_equity_losses > 0 &&
                <WaterfallRow label="Less: LTCL set-off"   amount={-ltcg_equity_losses} negative />}
              {stcg_equity_net < 0 &&
                <WaterfallRow label="Less: Remaining STCL" amount={stcg_equity_net}   negative />}
              <WaterfallRow label="Net LTCG"             amount={ltcg_equity_net}    isSubtotal />
              {ltcg_exempt_used > 0 &&
                <WaterfallRow label="Less: ₹1.25L Exemption" amount={-ltcg_exempt_used} faint />}
              <WaterfallRow label="Taxable LTCG"         amount={ltcg_taxable}       isSubtotal />
              {ltcg_taxable > 0 && <>
                <WaterfallRow label="LTCG Tax @ 12.5%"   amount={ltcg_tax_before_cess} faint />
                <WaterfallRow label="+ Cess 4%"           amount={ltcg_cess}            faint />
                <WaterfallRow label="LTCG Total Tax"      amount={ltcg_total_tax}       isSubtotal />
              </>}
            </>
          )}

          {hasDebt && (
            <>
              <p className="text-purple-400 text-[10px] font-semibold uppercase tracking-wider pt-2">Debt Funds (Slab Rate)</p>
              <WaterfallRow label="Debt Gains"           amount={debt_slab_gains}    positive />
              <WaterfallRow label={`Tax @ ${(slab_rate*100).toFixed(0)}% + 4% Cess`} amount={debt_slab_tax} isSubtotal />
            </>
          )}

          <div className="border-t border-border/60 mt-2 pt-2">
            <WaterfallRow label="GRAND TOTAL TAX" amount={total_tax} isTotal />
          </div>
        </div>
      </div>
    </div>
  )
}
