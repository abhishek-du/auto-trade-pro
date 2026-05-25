import { formatINR } from '../../utils/indianFormat'

function MetricCard({ label, value, sub, colorClass = 'text-slate-100', bgClass = '', small = false }) {
  return (
    <div className={`rounded-xl border border-border p-4 space-y-1 ${bgClass}`} style={{ background: bgClass ? undefined : '#0a0f1c' }}>
      <p className="text-muted text-[9px] uppercase tracking-widest">{label}</p>
      <p className={`font-bold tabular-nums ${small ? 'text-base' : 'text-xl'} ${colorClass}`}>{value}</p>
      {sub && <p className="text-muted text-[10px]">{sub}</p>}
    </div>
  )
}

function SectionHeader({ children }) {
  return (
    <p className="text-muted text-[9px] uppercase tracking-widest pt-1 pb-0.5 border-b border-border/40">
      {children}
    </p>
  )
}

export default function TaxSummaryCards({ taxSummary }) {
  if (!taxSummary) return null

  const {
    stcg_equity_gains, stcg_equity_losses, stcg_equity_net, stcg_total_tax,
    ltcg_equity_gains, ltcg_equity_losses, ltcg_equity_net,
    ltcg_exempt_used, ltcg_exempt_remaining, ltcg_taxable, ltcg_total_tax,
    debt_slab_gains, debt_slab_tax, slab_rate,
    total_tax, effective_tax_rate,
    stcl_carried_forward, ltcl_carried_forward, total_loss_carried,
  } = taxSummary

  const noActivity = stcg_equity_gains === 0 && ltcg_equity_gains === 0 && debt_slab_gains === 0
                     && stcg_equity_losses === 0 && ltcg_equity_losses === 0

  return (
    <div className="space-y-3">
      {noActivity && (
        <div className="rounded-xl border border-border/40 px-5 py-4 text-center text-muted text-sm" style={{ background: '#0a0f1c' }}>
          No realised transactions in this financial year.
        </div>
      )}

      {/* ROW 1 — Gains overview */}
      <SectionHeader>Realised Gains &amp; Losses</SectionHeader>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="STCG (Equity)"
          value={formatINR(stcg_equity_net, 0)}
          sub={`₹${(stcg_equity_gains/1000).toFixed(0)}K gain − ₹${(stcg_equity_losses/1000).toFixed(0)}K loss`}
          colorClass={stcg_equity_net >= 0 ? 'text-amber-400' : 'text-profit'}
        />
        <MetricCard
          label="LTCG (Equity)"
          value={formatINR(ltcg_equity_net, 0)}
          sub={`₹${(ltcg_equity_gains/1000).toFixed(0)}K gain − ₹${(ltcg_equity_losses/1000).toFixed(0)}K loss`}
          colorClass={ltcg_equity_net >= 0 ? 'text-blue-400' : 'text-profit'}
        />
        <MetricCard
          label="LTCG Exempt Used"
          value={formatINR(ltcg_exempt_used, 0)}
          sub={`₹${(ltcg_exempt_remaining/1000).toFixed(0)}K remaining of ₹1.25L`}
          colorClass="text-profit"
        />
        <MetricCard
          label="Debt / Slab Gains"
          value={formatINR(debt_slab_gains, 0)}
          sub={`Taxed at ${(slab_rate * 100).toFixed(0)}% slab`}
          colorClass="text-purple-400"
        />
      </div>

      {/* ROW 2 — Tax */}
      <SectionHeader>Tax Breakdown (incl. 4% Cess)</SectionHeader>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="STCG Tax (20%)"
          value={formatINR(stcg_total_tax, 0)}
          sub="Section 111A"
          colorClass={stcg_total_tax > 0 ? 'text-amber-400' : 'text-muted'}
        />
        <MetricCard
          label="LTCG Tax (12.5%)"
          value={formatINR(ltcg_total_tax, 0)}
          sub={`On ₹${(ltcg_taxable/1000).toFixed(0)}K taxable`}
          colorClass={ltcg_total_tax > 0 ? 'text-blue-400' : 'text-muted'}
        />
        <MetricCard
          label="Debt Tax (Slab)"
          value={formatINR(debt_slab_tax, 0)}
          sub={`Section 50AA / 112A`}
          colorClass={debt_slab_tax > 0 ? 'text-purple-400' : 'text-muted'}
        />
        <div
          className={`rounded-xl border p-4 space-y-1 ${
            total_tax > 0 ? 'border-red-500/30' : 'border-profit/30'
          }`}
          style={{ background: total_tax > 0 ? 'rgba(239,68,68,0.06)' : 'rgba(34,197,94,0.06)' }}
        >
          <p className="text-muted text-[9px] uppercase tracking-widest">Total Tax Payable</p>
          <p className={`font-bold text-2xl tabular-nums ${total_tax > 0 ? 'text-red-400' : 'text-profit'}`}>
            {formatINR(total_tax, 0)}
          </p>
          <p className="text-muted text-[10px]">Eff. rate: {effective_tax_rate.toFixed(1)}%</p>
        </div>
      </div>

      {/* ROW 3 — Losses to carry forward */}
      {total_loss_carried > 0 && (
        <>
          <SectionHeader>Losses to Carry Forward (up to 8 FYs)</SectionHeader>
          <div className="grid grid-cols-3 gap-3">
            <MetricCard
              label="STCL Carry Forward"
              value={formatINR(stcl_carried_forward, 0)}
              sub="Can offset STCG + LTCG"
              colorClass="text-profit"
              small
            />
            <MetricCard
              label="LTCL Carry Forward"
              value={formatINR(ltcl_carried_forward, 0)}
              sub="Can only offset LTCG"
              colorClass="text-profit"
              small
            />
            <MetricCard
              label="Total Loss Carried"
              value={formatINR(total_loss_carried, 0)}
              sub="File ITR on time to claim"
              colorClass="text-profit"
              small
            />
          </div>
        </>
      )}
    </div>
  )
}
